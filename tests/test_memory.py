from sim7200.memory import Address, MemoryImage, parse_address


def test_parse_address_forms():
    assert parse_address("I0.0") == Address("I", "bit", 0, 0)
    assert parse_address("Q13.7") == Address("Q", "bit", 13, 7)
    assert parse_address("VB213") == Address("V", "byte", 213)
    assert parse_address("VW22") == Address("V", "word", 22)
    assert parse_address("VD216") == Address("V", "dword", 216)
    assert parse_address("&VB100") == Address("V", "byte", 100)
    assert parse_address("T109") == Address("T", "timer", 109)
    assert parse_address("SM0.5") == Address("SM", "bit", 0, 5)
    assert parse_address("L0.2") == Address("L", "bit", 0, 2)
    assert parse_address("100") is None  # literal, not an address


def test_vw_write_visible_through_vb_and_bit_views():
    mem = MemoryImage()
    mem.write_word(Address("V", "word", 200), 0x1234)
    assert mem.read_byte(Address("V", "byte", 200)) == 0x12
    assert mem.read_byte(Address("V", "byte", 201)) == 0x34
    # 0x12 = 0b00010010 -> bit 1 and bit 4 set
    assert mem.read_bit(Address("V", "bit", 200, 1)) is True
    assert mem.read_bit(Address("V", "bit", 200, 4)) is True
    assert mem.read_bit(Address("V", "bit", 200, 0)) is False


def test_vd_write_overlaps_two_words_and_four_bytes():
    mem = MemoryImage()
    mem.write_dword(Address("V", "dword", 216), 31001)
    # 31001 = 0x00007919
    assert mem.read_byte(Address("V", "byte", 216)) == 0x00
    assert mem.read_byte(Address("V", "byte", 217)) == 0x00
    assert mem.read_byte(Address("V", "byte", 218)) == 0x79
    assert mem.read_byte(Address("V", "byte", 219)) == 0x19
    assert mem.read_word(Address("V", "word", 218)) == 0x7919


def test_bit_write_does_not_disturb_sibling_bits():
    mem = MemoryImage()
    mem.write_bit(Address("V", "bit", 224, 0), True)
    mem.write_bit(Address("V", "bit", 224, 1), True)
    mem.write_bit(Address("V", "bit", 224, 0), False)
    assert mem.read_bit(Address("V", "bit", 224, 0)) is False
    assert mem.read_bit(Address("V", "bit", 224, 1)) is True


def test_l_memory_is_call_scoped():
    mem = MemoryImage()
    mem.write_bit(Address("L", "bit", 0, 0), True)
    mem.push_frame()
    assert mem.read_bit(Address("L", "bit", 0, 0)) is False  # fresh frame, not inherited
    mem.write_bit(Address("L", "bit", 0, 0), True)
    mem.pop_frame()
    assert mem.read_bit(Address("L", "bit", 0, 0)) is True  # caller's frame restored intact


def test_set_reset_bit_sequence():
    mem = MemoryImage()
    mem.set_bit_seq(Address("V", "bit", 1129, 0), 3, True)
    assert mem.read_bit(Address("V", "bit", 1129, 0)) is True
    assert mem.read_bit(Address("V", "bit", 1129, 1)) is True
    assert mem.read_bit(Address("V", "bit", 1129, 2)) is True
    assert mem.read_bit(Address("V", "bit", 1129, 3)) is False


def test_resolve_operand_literal_vs_address():
    mem = MemoryImage()
    mem.write_byte(Address("V", "byte", 212), 5)
    assert mem.resolve_operand("0") == 0
    assert mem.resolve_operand("VB212") == 5
