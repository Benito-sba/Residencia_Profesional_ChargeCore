import csv
import io
import unittest

from openpyxl import load_workbook

from app.routers import reports


class SpreadsheetFormulaSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_csv_export_escapes_formula_strings_and_preserves_numbers(self):
        values = ("=1+1", "\t+SUM(A1:A2)", " -1+1", "@A1", "\x11=1+1", -12)
        rows = [{"value": value} for value in values]
        response = await reports._export_report_csv(rows, ["value"], "safe.csv")
        content = b"".join([chunk async for chunk in response.body_iterator])
        parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))

        self.assertEqual(
            [row[0] for row in parsed[1:]],
            [
                "'=1+1",
                "'\t+SUM(A1:A2)",
                "' -1+1",
                "'@A1",
                "'\x11=1+1",
                "-12",
            ],
        )

    async def test_excel_export_stores_formula_strings_as_text(self):
        values = ("=1+1", "\t+SUM(A1:A2)", " -1+1", "@A1")
        rows = [{"value": value} for value in values]
        response = await reports._export_report_excel(rows, ["value"], "safe.xlsx")
        content = b"".join([chunk async for chunk in response.body_iterator])
        workbook = load_workbook(io.BytesIO(content), data_only=False)
        sheet = workbook.active

        for row_number, value in enumerate(values, start=2):
            with self.subTest(value=value):
                cell = sheet.cell(row=row_number, column=1)
                self.assertEqual(cell.value, "'" + value)
                self.assertEqual(cell.data_type, "s")

    async def test_general_csv_export_escapes_untrusted_section_values(self):
        sections = [(
            "Clientes",
            [{"nombre": " =HYPERLINK(\"https://example.invalid\")"}],
            [("nombre", "Nombre")],
            ["Nombre"],
        )]
        response = await reports._export_general_report_csv({}, sections, "safe.csv")
        content = b"".join([chunk async for chunk in response.body_iterator])
        parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))

        self.assertEqual(parsed[-1][0], "' =HYPERLINK(\"https://example.invalid\")")


if __name__ == "__main__":
    unittest.main()
