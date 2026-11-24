"""Meaningful structural and regression tests for math30 conversion."""

from pathlib import Path
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

from xmcd2md.math_renderer import MathRenderer


def xml(source: str) -> ET.Element:
    return ET.fromstring(source)


class MathRendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = MathRenderer()

    def render(self, source):
        return self.renderer.render(xml(source))

    def test_definition_symbolic_and_saved_numeric_result_all_survive(self):
        source = '''<math><define><id subscript="23">R</id><eval>
          <symEval style="equals" hide-keywords="true"><apply><mult/><id subscript="0">r</id><id>L</id></apply>
          <command><sequence><id>explicit</id><id>ALL</id></sequence></command>
          <symResult><apply><mult/><real>0.096</real><real>77</real></apply></symResult></symEval>
          <result><real>7.392</real></result></eval></define></math>'''
        self.assertEqual(self.render(source), r"R_{23} \coloneqq r_{0} \cdot L = 0.096 \cdot 77 = 7.392")
        self.assertEqual(self.renderer.warnings, [])

    def test_saved_result_is_never_recalculated_or_rounded(self):
        output = self.render('<eval><apply><plus/><real>1</real><real>2</real></apply><result><real>99.59999999999999987</real></result></eval>')
        self.assertEqual(output, '1 + 2 = 99.59999999999999987')

    def test_missing_and_encoded_results_are_explicit(self):
        self.assertIn('saved result unavailable', self.render('<eval><id>x</id></eval>'))
        self.assertTrue(self.renderer.warnings)
        self.assertIn('encoded result unsupported', self.render('<result encoding="base64">QUJD</result>'))
        self.assertIn('QUJD', self.render('<result>QUJD</result>'))

    def test_literal_subscript_differs_from_underscore_in_name(self):
        self.assertEqual(self.render('<id subscript="2^2">X_1</id>'), r'\mathrm{X\_1}_{\mathrm{2\text{\textasciicircum{}}2}}')
        self.assertEqual(self.render('<id subscript="a">τ</id>'), r'\tau_{\mathrm{a}}')

    @unittest.skipUnless(shutil.which('pandoc'), 'Optional MathML compatibility check requires Pandoc')
    def test_literal_caret_survives_mathml_as_text_not_a_power(self):
        formula = self.render('<id subscript="1^2">x</id>')
        result = subprocess.run(['pandoc', '--from=markdown', '--to=html', '--mathml'],
                                input='$$' + formula + '$$', text=True,
                                encoding='utf-8', capture_output=True, check=True)
        self.assertEqual(result.stderr, '')
        markup = ET.fromstring(result.stdout)
        ns = '{http://www.w3.org/1998/Math/MathML}'
        self.assertIn('^', [node.text for node in markup.iter(ns + 'mtext')])
        self.assertEqual(list(markup.iter(ns + 'msup')), [])

    def test_precedence_does_not_change_arithmetic(self):
        self.assertEqual(self.render('<apply><mult/><apply><plus/><id>a</id><id>b</id></apply><id>c</id></apply>'), r'\left(a + b\right) \cdot c')
        self.assertEqual(self.render('<apply><minus/><id>a</id><apply><plus/><id>b</id><id>c</id></apply></apply>'), r'a - \left(b + c\right)')
        self.assertEqual(self.render('<apply><pow/><real>-2</real><real>2</real></apply>'), r'{\left(-2\right)}^{2}')
        self.assertEqual(self.render('<apply><pow/><apply><pow/><id>x</id><id>y</id></apply><id>z</id></apply>'), r'{\left({x}^{y}\right)}^{z}')

    def test_matrix_order_is_column_major(self):
        self.assertEqual(self.render('<matrix rows="2" cols="3"><real>1</real><real>4</real><real>2</real><real>5</real><real>3</real><real>6</real></matrix>'), r'\begin{bmatrix}1 & 2 & 3 \\ 4 & 5 & 6\end{bmatrix}')

    def test_malformed_matrix_retains_entries(self):
        output = self.render('<matrix rows="2" cols="2"><real>42</real></matrix>')
        self.assertIn('invalid entries', output)
        self.assertIn('42', output)
        self.assertTrue(self.renderer.warnings)

    def test_complex_numbers_follow_explicit_or_worksheet_symbol(self):
        self.renderer.imaginary_symbol = 'j'
        self.assertEqual(self.render('<complex><real>2</real><imag>-3</imag></complex>'), r'2 - 3\,\mathrm{j}')
        self.assertEqual(self.render('<imag symbol="i">1</imag>'), r'\mathrm{i}')
        self.assertEqual(self.render('<complex><imag>-1</imag></complex>'), r'-\mathrm{j}')

    def test_units_are_not_duplicated(self):
        source = '''<eval><apply><id>arg</id><id>U</id></apply><unitOverride><id>deg</id></unitOverride>
          <result><unitedValue><real>90</real><unitMonomial><unitReference unit="degree"/></unitMonomial></unitedValue></result></eval>'''
        self.assertEqual(self.render(source), r'\arg\left(U\right) = 90\,{}^\circ')

    def test_scientific_notation_preserves_mantissa(self):
        self.assertEqual(self.render('<real>-2.2204460492503131E-16</real>'), r'-2.2204460492503131\times 10^{-16}')

    def test_function_arguments_and_indices(self):
        self.assertEqual(self.render('<apply><id>f</id><sequence><id>x</id><id>y</id></sequence></apply>'), r'f\left(x, y\right)')
        self.assertEqual(self.render('<apply><indexer/><id>A</id><sequence><real>0</real><real>1</real></sequence></apply>'), r'{A}_{0, 1}')

    def test_provenance_metadata_does_not_become_math(self):
        source = '''<ml:provenance xmlns:ml="http://schemas.mathsoft.com/math30" xmlns:p="http://schemas.mathsoft.com/provenance10">
          <p:originRef href="private"/><ml:define><ml:id>x</ml:id><ml:real>3</ml:real></ml:define></ml:provenance>'''
        self.assertEqual(self.render(source), r'x \coloneqq 3')
        self.assertEqual(self.renderer.warnings, [])

    def test_unknown_nodes_and_operators_preserve_operands_each_time(self):
        for _ in range(2):
            value = self.render('<apply><newOperator/><id>x</id><real>17</real></apply>')
            self.assertIn('newOperator', value)
            self.assertIn('x, 17', value)
        self.assertEqual(len(self.renderer.warnings), 2)
        self.assertIn('42', self.render('<future><real>42</real></future>'))

    def test_sum_and_integral_unambiguous_lambda_layout(self):
        expression = '<lambda><boundVars><id>k</id></boundVars><apply><pow/><id>k</id><real>2</real></apply></lambda><real>1</real><id>n</id>'
        self.assertEqual(self.render('<apply><sum/>' + expression + '</apply>'), r'\sum_{k=1}^{n} {k}^{2}')
        self.assertEqual(self.render('<apply><integral/>' + expression + '</apply>'), r'\int_{1}^{n} {k}^{2}\,\mathrm{d}k')
        self.assertEqual(self.renderer.warnings, [])

    def test_unknown_sum_layout_is_not_guessed(self):
        self.assertIn('unsupported sum layout', self.render('<apply><sum/><id>x</id><id>y</id></apply>'))
        self.assertTrue(self.renderer.warnings)

    def test_all_sample_formulas_render_without_unknown_constructs(self):
        sample_files = list(Path(__file__).resolve().parents[1].glob('*.xmcd'))
        if not sample_files:
            self.skipTest('User sample worksheets are not distributed with the package')
        count = 0
        for path in sample_files:
            for element in ET.parse(path).getroot().iter('{http://schemas.mathsoft.com/worksheet30}math'):
                with self.subTest(worksheet=path.name, formula=count):
                    self.assertTrue(self.renderer.render(element))
                count += 1
        self.assertGreater(count, 500)
        self.assertEqual(self.renderer.warnings, [])


if __name__ == '__main__':
    unittest.main()
