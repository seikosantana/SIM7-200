"""NiceGUI app: one asyncio process running the UI, the scan-cycle loop, and the
Modbus TCP server all against a single shared MemoryImage -- no threads, per
NiceGUI's own guidance for anything touching shared state.

Styling is a dark control-panel treatment on purpose, not a generic dark theme:
the semantic colors (green=energized/run, amber=accent/caution, sky=changed-
this-scan, red=fault) follow the same convention real HMI/SCADA screens use, so
"green means running" reads the same way here as it would on the actual plant's
touchscreens.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from nicegui import app, ui

from .interpreter import Interpreter
from .labels import Label, load_labels
from .library import install_t_monitor_library
from .memory import AREA_SIZES, Address, parse_address
from .modbus_server import Sim7200ModbusServer
from .project import Project, build_interpreter, load_project, scan_order_sbr_nums

ROOT = Path(__file__).resolve().parent.parent
# Point these at your PLC project: SIM7200_PLC_DIR holds the Memory Map xlsx files (GUI labels),
# SIM7200_PROJECTS_DIR holds the *.sim.json project files.
PLC_DIR = Path(os.environ.get("SIM7200_PLC_DIR", ROOT.parent / "PLC"))
PROJECTS_DIR = Path(os.environ.get("SIM7200_PROJECTS_DIR", ROOT / "projects"))
MEMORY_MAP_BY_PLC = {1: "Memory Map.xlsx", 2: "Memory Map PLC2.xlsx"}

_CSS = """
body { background: #0b0f14 !important; }
.sim-panel { background: #131820; border: 1px solid #232b35; border-radius: 10px; padding: 10px 12px; }
.sim-label { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: #7c8698; font-weight: 600; }
.sim-mono td, .sim-mono th { font-family: ui-monospace, Consolas, "Cascadia Code", monospace !important;
  font-variant-numeric: tabular-nums; font-size: 12px !important; padding: 3px 10px !important; }
.sim-chip { font-family: ui-monospace, Consolas, "Cascadia Code", monospace !important; font-size: 11px !important; }
.sim-io-col { min-width: 118px; max-width: 160px; }
.sim-col-head { font-size: 10px; font-weight: 700; letter-spacing: .04em; color: #93a1b5;
  border-bottom: 1px solid #232b35; padding-bottom: 2px; margin-bottom: 2px; }
:focus-visible { outline: 2px solid #e8a33d; outline-offset: 1px; }
"""


def _fuzzy_score(query: str, text: str) -> float | None:
    """None if `query`'s characters don't all occur in `text` in order (not
    necessarily contiguous); otherwise a score rewarding matches whose
    characters land close together and near the start -- the same "type any
    subset of characters in order" idiom fzf/command palettes use, scored
    simply enough that no fuzzy-matching library earns its keep for filtering
    a few hundred rows. (A whole-string similarity ratio was tried first and
    rejected: it penalized a dead-on address match against a short label in
    favor of a partial match against a long one, purely because the longer
    combined text made the *overall* strings look more alike.)"""
    pos = -1
    first = None
    for ch in query:
        pos = text.find(ch, pos + 1)
        if pos == -1:
            return None
        if first is None:
            first = pos
    span = pos - first + 1
    return len(query) / span - first * 1e-6


def fuzzy_filter(items: list, text_fn, query: str) -> list:
    """Keeps items whose text_fn(item) fuzzy-matches `query`, best match first.
    Empty query returns items unchanged."""
    query = query.strip().lower()
    if not query:
        return list(items)
    scored = []
    for item in items:
        score = _fuzzy_score(query, text_fn(item).lower())
        if score is not None:
            scored.append((score, item))
    scored.sort(key=lambda p: -p[0])
    return [item for _, item in scored]


class SimState:
    """All mutable simulator state, decoupled from any one browser tab -- every
    connected client renders the same shared simulation."""

    def __init__(self) -> None:
        self.proj: Project | None = None
        self.interp: Interpreter | None = None
        self.order: list[int] = []
        self.running = False
        self.tick_seconds = 0.3
        self.modbus: Sim7200ModbusServer | None = None
        self.labels: dict[str, Label] = {}
        self.timer_labels: dict[int, Label] = {}
        self.i_addrs: list[tuple[Address, Label]] = []
        self.q_addrs: list[tuple[Address, Label]] = []
        self.table_addrs: list[tuple[Address, Label]] = []
        self.changed: dict[str, set[int]] = {}
        self.error: str | None = None

    async def load(self, project_name: str) -> None:
        await self.unload()
        proj, subs_by_num, subs_by_name = load_project(PROJECTS_DIR / project_name)
        interp = build_interpreter(proj, subs_by_num)
        if proj.plc_id == 2:
            # Every PLC2 project in this repo has T_MONITOR (or nothing) at SBR1/SBR2 --
            # see library.py's install_t_monitor_library docstring.
            install_t_monitor_library(interp, proj.fake_slaves)
        self.order = scan_order_sbr_nums(proj, subs_by_name)
        self.proj = proj
        self.interp = interp
        self.error = None

        xlsx = PLC_DIR / MEMORY_MAP_BY_PLC.get(proj.plc_id, "")
        self.labels, self.timer_labels = load_labels(xlsx) if xlsx.exists() else ({}, {})
        self.i_addrs = self._labeled_io_addrs("I")
        self.q_addrs = self._labeled_io_addrs("Q")
        self.table_addrs = self._build_table_addrs()

        if proj.modbus_tcp_port:
            self.modbus = Sim7200ModbusServer(
                interp.mem, port=proj.modbus_tcp_port,
                word_registers=frozenset(proj.modbus_word_registers),
            )
            await self.modbus.start()

    async def unload(self) -> None:
        self.running = False
        if self.modbus is not None:
            await self.modbus.stop()
            self.modbus = None
        self.interp = None
        self.proj = None
        self.i_addrs, self.q_addrs, self.table_addrs = [], [], []

    def _labeled_io_addrs(self, area: str) -> list[tuple[Address, Label]]:
        out = []
        for byte in range(AREA_SIZES[area]):
            for bit in range(8):
                addr = Address(area, "bit", byte, bit)
                lbl = self.labels.get(str(addr))
                if lbl is not None:
                    out.append((addr, lbl))
        return out

    def _build_table_addrs(self) -> list[tuple[Address, Label]]:
        entries = []
        for addr_str, lbl in self.labels.items():
            if addr_str.startswith("V"):
                addr = parse_address(addr_str)
                if addr is not None:
                    entries.append((addr, lbl))
        entries.sort(key=lambda t: t[0].byte)
        for byte in range(AREA_SIZES["M"]):
            entries.append((Address("M", "byte", byte), Label("", "")))
        for num, lbl in sorted(self.timer_labels.items()):
            entries.append((Address("T", "timer", num), lbl))
        return entries

    def step_once(self) -> None:
        if self.interp is None:
            return
        before = {area: bytes(buf) for area, buf in self.interp.mem.areas.items()}
        try:
            self.interp.run_scan(self.order)
        except Exception as exc:  # surface interpreter faults in the UI, don't kill the scan loop
            self.running = False
            self.error = f"{type(exc).__name__}: {exc}"
            return
        self.error = None
        self.changed = {}
        for area, old in before.items():
            new = self.interp.mem.areas[area]
            diff = {i for i in range(len(old)) if old[i] != new[i]}
            if diff:
                self.changed[area] = diff

    def is_changed(self, addr: Address) -> bool:
        if addr.area == "T":
            return False
        start, end = addr.byte_span()
        return bool(self.changed.get(addr.area, set()) & set(range(start, end + 1)))

    def filtered_io(self, area: str, query: str) -> list[tuple[Address, Label]]:
        items = self.i_addrs if area == "I" else self.q_addrs
        return fuzzy_filter(items, lambda t: f"{t[0]} {t[1].tag} {t[1].description}", query)

    def io_grouped(self, area: str) -> dict[int, list[tuple[Address, Label]]]:
        """Labeled addresses for `area`, grouped by byte and ordered by bit --
        one physical I/O byte per group, matching how the real symbol table
        (and the terminal strips it maps to) is organized."""
        items = self.i_addrs if area == "I" else self.q_addrs
        groups: dict[int, list[tuple[Address, Label]]] = {}
        for addr, lbl in items:
            groups.setdefault(addr.byte, []).append((addr, lbl))
        return dict(sorted(groups.items()))

    def table_rows(self, query: str) -> list[dict]:
        if self.interp is None:
            return []
        matches = fuzzy_filter(self.table_addrs, lambda t: f"{t[0]} {t[1].tag} {t[1].description}", query)
        rows = []
        for addr, label in matches:
            rows.append({
                "address": str(addr),
                "label": label.text,
                "value": self.interp.mem.read(addr),
                "chg": "●" if self.is_changed(addr) else "",
            })
        return rows


state = SimState()


async def _scan_loop() -> None:
    while True:
        if state.running:
            state.step_once()
        await asyncio.sleep(state.tick_seconds)


def _io_chip_classes(on: bool, interactive: bool, vis: bool) -> str:
    # "hidden" is folded into this string (not left to set_visibility() alone) because
    # set_visibility() is backed by a BindableProperty that no-ops when the value doesn't
    # actually change -- so on any tick where a chip's visibility state is unchanged from
    # the tick before, set_visibility(vis) silently does nothing, while the *unconditional*
    # classes(replace=...) call right before it still wipes the class list clean. Net
    # effect: a filtered-out chip flashed hidden for exactly one tick (the transition),
    # then popped back to visible on every tick after. Managing "hidden" here makes every
    # tick self-consistent regardless of whether visibility just changed.
    base = "sim-chip w-full justify-start overflow-hidden text-ellipsis whitespace-nowrap "
    base += "cursor-pointer" if interactive else "cursor-default opacity-90"
    if on:
        base += " !bg-emerald-900 !text-emerald-300 border border-emerald-700"
    else:
        base += " !bg-slate-800 !text-slate-400 border border-slate-700"
    if not vis:
        base += " hidden"
    return base


@ui.page("/")
def index() -> None:
    ui.dark_mode(True)

    # Chip widgets are built ONCE per project load (build_io_panel) and only ever
    # mutated in place afterwards (sync_io_panels) -- never cleared/recreated on the
    # 0.5s refresh tick. Clearing+rebuilding ~150 chip elements every tick was the
    # actual cause of "toggling an input doesn't work": a click landing while that
    # tick's rebuild was in flight destroyed the very element being clicked, so the
    # click never reached a live handler. Static widgets, dynamic props -- no more
    # race between a user's click and the periodic re-render.
    i_chips: dict[Address, ui.chip] = {}
    q_chips: dict[Address, ui.chip] = {}
    i_cols: dict[int, ui.column] = {}
    q_cols: dict[int, ui.column] = {}

    # Filter text is captured via on_change into a plain dict, never re-read from
    # the ui.input elements' .value later. ui.input ships with loopback=False (an
    # optimization that skips echoing typed keystrokes back to the client, since
    # the client already has them) -- but that leaves the server's cached client-echo
    # state ("is what the server has == what the client already has") to go stale
    # under this app's own high-frequency unrelated element updates (~200 chip
    # .classes()/.set_visibility() calls every tick), which was intermittently
    # making a periodic re-read of i_filter.value see a stale "" even though the
    # DOM still showed the typed text. Capturing the value at the moment the
    # on_change event actually hands it to us sidesteps that entirely.
    filter_text = {"i": "", "q": "", "mem": ""}

    with ui.column().classes("w-full min-h-screen bg-slate-950 text-slate-200 p-3 gap-3"):
        ui.label("SIM7-200").classes("text-base font-bold tracking-widest text-amber-500")

        with ui.row().classes("items-center gap-2 flex-wrap"):
            project_files = sorted(p.name for p in PROJECTS_DIR.glob("*.sim.json"))
            project_select = ui.select(
                project_files, label="Project", value=project_files[0] if project_files else None
            ).props("dense options-dense").classes("w-72")

            async def do_load() -> None:
                if not project_select.value:
                    return
                await state.load(project_select.value)
                status_label.set_text(f"loaded {project_select.value}  (plc_id={state.proj.plc_id}, port={state.proj.modbus_tcp_port})")
                build_io_panel(i_container, "I", i_chips, i_cols, interactive=True)
                build_io_panel(q_container, "Q", q_chips, q_cols, interactive=False)
                build_slaves_panel()
                refresh_dynamic()

            ui.button("Load", on_click=do_load).props("dense unelevated color=amber-8")
            ui.switch("Run").props("dense").bind_value(state, "running")
            ui.button("Step", on_click=lambda: (state.step_once(), refresh_dynamic())).props("dense outline color=amber-8")
            ui.number("tick s", min=0.05, step=0.05, format="%.2f").props("dense").bind_value(state, "tick_seconds").classes("w-20")
            scan_count_label = ui.label().classes("text-xs text-slate-400 font-mono")
            status_label = ui.label().classes("text-xs text-slate-500")

        error_label = ui.label().classes("text-xs text-rose-400")

        with ui.column().classes("sim-panel gap-1 w-full"):
            ui.label("Inputs").classes("sim-label")
            i_filter = ui.input(placeholder="filter…", on_change=lambda e: on_filter_change("i", e.value))
            i_filter.props("dense borderless").classes("text-xs w-72")
            i_container = ui.row().classes("flex-wrap gap-3 mt-1 items-start")

        with ui.column().classes("sim-panel gap-1 w-full"):
            ui.label("Outputs (read-only)").classes("sim-label")
            q_filter = ui.input(placeholder="filter…", on_change=lambda e: on_filter_change("q", e.value))
            q_filter.props("dense borderless").classes("text-xs w-72")
            q_container = ui.row().classes("flex-wrap gap-3 mt-1 items-start")

        with ui.column().classes("sim-panel gap-1 w-full max-w-md"):
            ui.label("Fake Slaves").classes("sim-label")
            slaves_container = ui.row().classes("flex-wrap gap-1 mt-1")

        with ui.column().classes("sim-panel gap-1 w-full"):
            ui.label("Memory (V / M / T)").classes("sim-label")
            mem_filter = ui.input(placeholder="filter address or label…", on_change=lambda e: on_filter_change("mem", e.value))
            mem_filter.props("dense borderless").classes("text-xs w-72")
            mem_table = ui.table(
                columns=[
                    {"name": "address", "label": "Addr", "field": "address", "align": "left"},
                    {"name": "label", "label": "Label", "field": "label", "align": "left"},
                    {"name": "value", "label": "Value", "field": "value", "align": "right"},
                    {"name": "chg", "label": "", "field": "chg", "align": "center"},
                ],
                rows=[],
                row_key="address",
                pagination=15,
            ).classes("sim-mono w-full mt-1")
            mem_table.props("dense flat")

        def toggle_input(addr: Address, value: bool) -> None:
            if state.interp is not None:
                state.interp.mem.write_bit(addr, value)
            sync_io_panels()

        def build_io_panel(container, area: str, chips: dict, cols: dict, interactive: bool) -> None:
            container.clear()
            chips.clear()
            cols.clear()
            with container:
                for byte, entries in state.io_grouped(area).items():
                    with ui.column().classes("sim-io-col gap-0.5") as col:
                        ui.label(f"{area}{byte}").classes("sim-col-head")
                        for addr, lbl in entries:
                            text = f"{addr.bit} | {lbl.tag}" if lbl.tag else str(addr.bit)
                            if interactive:
                                chip = ui.chip(
                                    text, selectable=True,
                                    on_selection_change=lambda e, a=addr: toggle_input(a, e.value),
                                )
                            else:
                                chip = ui.chip(text)
                            chip.props("dense")
                            if lbl.description:
                                chip.tooltip(lbl.description)
                            chips[addr] = chip
                    cols[byte] = col

        def build_slaves_panel() -> None:
            slaves_container.clear()
            with slaves_container:
                for key, value in sorted(state.proj.fake_slaves.items()):
                    def set_value(e, k=key) -> None:
                        state.proj.fake_slaves[k] = int(e.value)
                    ui.number(key, value=value, on_change=set_value).props("dense outlined").classes("w-28 font-mono text-xs")

        def on_filter_change(which: str, value: str | None) -> None:
            filter_text[which] = value or ""
            refresh_dynamic()

        def sync_io_panels() -> None:
            if state.interp is None:
                return
            mem = state.interp.mem
            for area, chips, cols, key, interactive in (
                ("I", i_chips, i_cols, "i", True),
                ("Q", q_chips, q_cols, "q", False),
            ):
                visible = {a for a, _ in state.filtered_io(area, filter_text[key])}
                col_visible: dict[int, bool] = {}
                for addr, chip in chips.items():
                    on = mem.read_bit(addr)
                    vis = addr in visible
                    chip.classes(replace=_io_chip_classes(on, interactive, vis))
                    if interactive:
                        chip.set_selected(on)
                    col_visible[addr.byte] = col_visible.get(addr.byte, False) or vis
                for byte, col in cols.items():
                    col.set_visibility(col_visible.get(byte, False))

        def refresh_dynamic() -> None:
            error_label.set_text(state.error or "")
            scan_count_label.set_text(f"scan #{state.interp.scan_count}" if state.interp is not None else "no project loaded")
            sync_io_panels()
            mem_table.rows = state.table_rows(filter_text["mem"])
            mem_table.update()

        ui.timer(0.5, refresh_dynamic)


app.on_startup(_scan_loop)
ui.add_css(_CSS, shared=True)


def run_gui(port: int = 8080) -> None:
    ui.run(title="SIM7-200", port=port, reload=False, show=True)
