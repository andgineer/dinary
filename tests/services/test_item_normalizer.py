import allure

from dinary.services.item_normalizer import normalize_item_name


@allure.epic("Services")
@allure.feature("Item Normalizer")
class TestNormalizeItemName:
    def test_lowercase(self):
        assert normalize_item_name("HLEB") == "hleb"

    def test_strip_grams(self):
        assert normalize_item_name("HLEB BELI 550G") == "hleb beli"

    def test_strip_kg(self):
        assert normalize_item_name("ŠARGAREPA 1KG") == "šargarepa"

    def test_strip_ml(self):
        assert normalize_item_name("JOGURT 180ML") == "jogurt"

    def test_strip_l(self):
        assert normalize_item_name("MLEKO 1L") == "mleko"

    def test_strip_kom(self):
        assert normalize_item_name("WC PAPIR 8KOM") == "wc papir"

    def test_strip_pc(self):
        assert normalize_item_name("ESPRESSO CUPS 6PC") == "espresso cups"

    def test_strip_pcs(self):
        assert normalize_item_name("FILTER BAGS 50PCS") == "filter bags"

    def test_decimal_comma_quantity(self):
        assert normalize_item_name("SIR 0,5KG") == "sir"

    def test_multiple_trailing_units(self):
        assert normalize_item_name("BELO VINO 750ML 6KOM") == "belo vino"

    def test_collapse_whitespace(self):
        assert normalize_item_name("HLEB  BELI") == "hleb beli"

    def test_leading_trailing_whitespace(self):
        assert normalize_item_name("  JOGURT 180ML  ") == "jogurt"

    def test_cyrillic_preserved(self):
        assert normalize_item_name("ХЛЕБ 500G") == "хлеб"

    def test_no_unit(self):
        assert normalize_item_name("JABUKA") == "jabuka"

    def test_already_lowercase(self):
        assert normalize_item_name("jogurt") == "jogurt"

    def test_cl_unit(self):
        assert normalize_item_name("VODA 33CL") == "voda"

    def test_number_in_name_not_stripped(self):
        # a number that is part of the name but not followed by a unit
        assert normalize_item_name("COLA ZERO 2L") == "cola zero"

    def test_lidl_barcode_suffix_kom(self):
        assert normalize_item_name("Rotkvica, veza/KOM/0082275") == "rotkvica, veza"

    def test_lidl_barcode_suffix_kg(self):
        assert normalize_item_name("Banane, rinfuz/KG/0080000") == "banane, rinfuz"

    def test_lidl_barcode_with_vat_code(self):
        assert normalize_item_name("Banane, rinfuz/KG/0080000 (E)") == "banane, rinfuz"

    def test_vat_code_stripped(self):
        assert normalize_item_name("MLEKO 1L (Đ)") == "mleko"

    def test_metro_leading_volume(self):
        assert normalize_item_name("0.33L COCA COLA SOK LM") == "coca cola sok lm"

    def test_metro_leading_volume_large(self):
        assert normalize_item_name("1000ML MC SOJA SOS KO") == "mc soja sos ko"
