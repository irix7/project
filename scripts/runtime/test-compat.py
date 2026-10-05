#!/usr/bin/env python3
"""Host-only unit tests for scripts/runtime/compat.py (issue #9).

Each translation exists because a stock tree source uses MIPSpro syntax
GNU cpp/as rejects; see the module docstring for the reason behind each.
Snippets are synthetic (ADR-0001).

Run: python3 scripts/runtime/test-compat.py
"""

import unittest

from compat import (
    CompatError,
    fix_unterminated_statement,
    retarget_knr_definition,
    rewrite_asm_source,
    rewrite_c_source,
)


class PragmaWeakTest(unittest.TestCase):
    def test_alias_becomes_an_assembler_weak_alias(self):
        text = "#pragma weak tcgetattr = _tcgetattr\nint tcgetattr(void) { return 0; }\n"
        result = rewrite_c_source(text)
        self.assertIn('__asm__(".weak tcgetattr\\n.set tcgetattr, _tcgetattr");', result)
        self.assertNotIn("#pragma", result)

    def test_macro_renaming_does_not_touch_the_alias_names(self):
        text = "#define tcgetattr _tcgetattr\n#include <x.h>\n#pragma weak tcgetattr = _tcgetattr\n"
        result = rewrite_c_source(text)
        self.assertIn('".weak tcgetattr\\n.set tcgetattr, _tcgetattr"', result)

    def test_plain_weak_declaration(self):
        result = rewrite_c_source("#pragma weak frobnicate\n")
        self.assertIn('__asm__(".weak frobnicate");', result)

    def test_indented_pragma_is_rewritten(self):
        result = rewrite_c_source("\t#pragma weak a = _a\n")
        self.assertIn(".weak a", result)

    def test_ordinary_pragmas_are_untouched(self):
        result = rewrite_c_source('#pragma ident "$Revision: 1.2 $"\n')
        self.assertEqual(result, '#pragma ident "$Revision: 1.2 $"\n')

    def test_almost_weak_is_untouched(self):
        result = rewrite_c_source("/* #pragma weakness */\n")
        self.assertEqual(result, "/* #pragma weakness */\n")


class WeakExtTest(unittest.TestCase):
    def test_alias_kept_when_the_symbol_is_not_defined_here(self):
        text = ".weakext bzero, _bzero\n"
        self.assertEqual(rewrite_asm_source(text), text)

    def test_alias_dropped_when_the_symbol_is_defined_here(self):
        text = ".weakext memset, __memset\nLEAF(memset)\n"
        result = rewrite_asm_source(text)
        self.assertIn(".weakext\tmemset\n", result)
        self.assertNotIn(", __memset", result)

    def test_label_definition_counts(self):
        text = ".weakext finitel, _finitel\nfinitel:\n"
        self.assertNotIn(", _finitel", rewrite_asm_source(text))

    def test_other_weakexts_are_untouched(self):
        text = ".weakext a, _a\n.weakext b, _b\n"
        self.assertEqual(rewrite_asm_source(text), text)


class AssemblerSyntaxTest(unittest.TestCase):
    def test_one_register_conversion_is_spelled_out(self):
        self.assertIn("cvt.d.w\t$f0,$f0", rewrite_asm_source("cvt.d.w\t$f0\n"))

    def test_long_integer_conversion_is_spelled_out(self):
        self.assertIn("cvt.d.l\t$f2,$f2", rewrite_asm_source("cvt.d.l\t$f2\t# comment\n"))

    def test_two_operand_conversion_is_untouched(self):
        text = "cvt.d.w\t$f0,$f4\n"
        self.assertEqual(rewrite_asm_source(text), text)

    def test_double_quoted_character_immediate(self):
        result = rewrite_asm_source('li\tt4, "0"\t# digit\n')
        self.assertIn("li\tt4, '0'", result)

    def test_double_quoted_words_in_comments_are_untouched(self):
        text = "* uses the instructions \"lwl\", \"lwr\"\n"
        self.assertEqual(rewrite_asm_source(text), text)

    def test_syscall_paste_casualty(self):
        result = rewrite_asm_source("\tli\t$2,SYS_ xpg4_recvmsg\n")
        self.assertIn("li\t$2,1227", result)

    def test_other_syscall_pastes_are_untouched(self):
        text = "\tli\t$2,SYS_write\n"
        self.assertEqual(rewrite_asm_source(text), text)


class SourceErrataTest(unittest.TestCase):
    def test_knr_definition_is_retargeted_and_aliased(self):
        text = "mq_open(name, oflag, mode, attrs)\nconst char *name;\n{\n}\n"
        result = retarget_knr_definition(text, "mq_open", "__mq_open_impl")
        self.assertIn('__asm__(".globl mq_open\\n.set mq_open, __mq_open_impl");', result)
        self.assertIn("__mq_open_impl(name, oflag, mode, attrs)", result)
        self.assertNotIn("\nmq_open(name", result)

    def test_retarget_requires_the_definition(self):
        with self.assertRaises(CompatError):
            retarget_knr_definition("int other(void);\n", "mq_open", "__impl")


class UnterminatedStatementTest(unittest.TestCase):
    """The upload's missing semicolon is found by syntax alone (ADR-0001).

    The matcher takes only the source text; it never carries the tree's
    statement. It repairs a single unambiguous call statement inside a block
    that is followed by a complete statement or the closing brace, and
    refuses to guess when no candidate or several candidates exist.
    """

    def test_call_before_a_closing_brace_gains_exactly_one_semicolon(self):
        text = "void emit(int value) {\n\tnote(value)\n}\n"
        self.assertEqual(
            fix_unterminated_statement(text),
            "void emit(int value) {\n\tnote(value);\n}\n",
        )

    def test_cast_call_mid_block_gains_exactly_one_semicolon(self):
        text = (
            "void emit(int value) {\n"
            "\t(void)note(value)\n"
            "\tadvance();\n"
            "}\n"
        )
        self.assertEqual(
            fix_unterminated_statement(text),
            "void emit(int value) {\n"
            "\t(void)note(value);\n"
            "\tadvance();\n"
            "}\n",
        )

    def test_terminated_statements_are_not_candidates(self):
        text = "void emit(int value) {\n\tnote(value);\n\tadvance();\n}\n"
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)

    def test_control_flow_headers_are_not_candidates(self):
        text = (
            "void emit(int value) {\n"
            "\tif (value)\n\t\tnote(value);\n"
            "\twhile (value)\n\t\tnote(value);\n"
            "\tswitch (value) {\n\t}\n"
            "}\n"
        )
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)

    def test_labels_directives_and_comments_are_not_candidates(self):
        text = (
            "void emit(int value) {\n"
            "again:\n"
            "#define note(value) advance(value)\n"
            "\t/* note(value) */\n"
            "\tadvance();\n"
            "}\n"
        )
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)

    def test_call_shaped_line_inside_a_comment_is_not_a_candidate(self):
        text = (
            "void emit(int value) {\n"
            "\t/*\n"
            "\tnote(value)\n"
            "\t*/\n"
            "\tadvance();\n"
            "}\n"
        )
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)

    def test_function_definition_header_is_not_a_candidate(self):
        text = "void note(int value)\n{\n\tadvance();\n}\n"
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)

    def test_lone_terminator_on_the_next_line_is_not_a_candidate(self):
        text = "void emit(int value) {\n\tnote(value)\n\t;\n}\n"
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)

    def test_initialiser_call_is_not_a_candidate(self):
        text = (
            "void emit(void) {\n"
            "\tint table[] = {\n"
            "\t\tmake(1)\n"
            "\t};\n"
            "}\n"
        )
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)

    def test_no_candidate_raises_compat_error(self):
        with self.assertRaises(CompatError):
            fix_unterminated_statement("int value;\n")

    def test_several_candidates_are_rejected_as_ambiguous(self):
        text = (
            "void emit(int value) {\n"
            "\tnote(value)\n"
            "\tadvance();\n"
            "\treport(value)\n"
            "}\n"
        )
        with self.assertRaises(CompatError):
            fix_unterminated_statement(text)


class TextSubsectionTest(unittest.TestCase):
    def test_init_subsection_becomes_a_section(self):
        result = rewrite_asm_source("\t.text .init\n")
        self.assertIn('.section .init,"ax",@progbits', result)

    def test_fini_subsection_becomes_a_section(self):
        result = rewrite_asm_source("\t.text .fini\n")
        self.assertIn('.section .fini,"ax",@progbits', result)

    def test_plain_text_directive_is_untouched(self):
        self.assertEqual(rewrite_asm_source("\t.text\n"), "\t.text\n")


if __name__ == "__main__":
    unittest.main()
