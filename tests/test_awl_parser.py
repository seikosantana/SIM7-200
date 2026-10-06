from sim7200.awl_parser import parse_file
from conftest import PLC_DIR


def test_parse_motor():
    sub = parse_file(PLC_DIR / "awl/plc1/MOTOR.awl")
    assert sub.name == "MOTOR"
    assert sub.sbr_num == 1
    assert sub.params == ["cmdRun", "flgFailing", "qRun"]
    assert len(sub.networks) == 2
    net1 = sub.networks[0]
    assert [ins.op for ins in net1.instructions] == ["LD", "="]
    assert net1.instructions[0].args == ["L0.0"]
    net2 = sub.networks[1]
    assert [ins.op for ins in net2.instructions] == ["LD", "R"]
    assert net2.instructions[1].args == ["L0.0", "1"]


def test_parse_buffer_silo_motors_orchestration():
    sub = parse_file(PLC_DIR / "awl/plc1/BUFFER_SILO_MOTORS.awl")
    assert sub.sbr_num == 27
    assert sub.params == []
    calls = [ins for net in sub.networks for ins in net.instructions if ins.op == "CALL"]
    assert len(calls) == 2
    assert calls[0].args[0] == "SBR1"
    assert calls[0].args[1:] == ["V1083.0", "V2071.0", "Q12.1"]


def test_parse_buffer_silo_ilocks_lps_lpp_ald():
    sub = parse_file(PLC_DIR / "awl/plc1/BUFFER_SILO_ILOCKS.awl")
    ops = [ins.op for net in sub.networks for ins in net.instructions]
    assert "ALD" in ops
    gudang = parse_file(PLC_DIR / "awl/plc1/GUDANG_ILOCKS.awl")
    ops2 = [ins.op for net in gudang.networks for ins in net.instructions]
    assert "LPS" in ops2 and "LPP" in ops2


def test_parse_t_monitor_full_file():
    sub = parse_file(PLC_DIR / "awl/plc2/T_MONITOR.awl")
    assert sub.sbr_num == 10
    assert len(sub.networks) == 18
    call_net3 = sub.networks[2].instructions
    call = [ins for ins in call_net3 if ins.op == "CALL"][0]
    assert call.args[0] == "SBR2"
    assert call.args == ["SBR2", "L63.7", "VB213", "VB214", "VD216", "1", "&VB200", "V227.0", "VB228"]


def test_instruction_keys_are_unique_and_stable():
    sub = parse_file(PLC_DIR / "awl/plc1/MOTOR.awl")
    keys = [ins.key for ins in sub.all_instructions()]
    assert keys == ["MOTOR#1.0", "MOTOR#1.1", "MOTOR#2.0", "MOTOR#2.1"]
