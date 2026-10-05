#!/usr/bin/env python3
"""Host-only unit tests for scripts/runtime/smake.py (issue #9).

The evaluator is the one non-obvious pure piece of the runtime rebuild: it
reads the tree's smake leaf makefiles at build time to recover the exact
CFILES/ASFILES the IRIX release selected for a library variant, so the
rebuild compiles the same set MIPSpro did. It never runs the tree's rules.

All snippets here are synthetic — no tree text is copied into the repository
(ADR-0001). The real makefiles are read only at build time.

Run: python3 scripts/runtime/test-smake.py
"""

import unittest

from smake import SmakeError, evaluate, expand, filter_match, substitute


class ExpansionTest(unittest.TestCase):
    def test_bare_reference_expands(self):
        self.assertEqual(expand("$(NAME)", {"NAME": "libc.a"}), "libc.a")

    def test_braces_expand(self):
        self.assertEqual(expand("${NAME}", {"NAME": "libc.a"}), "libc.a")

    def test_undefined_reference_is_empty(self):
        self.assertEqual(expand("[$(MISSING)]", {}), "[]")

    def test_nested_reference_expands_inside_the_value(self):
        self.assertEqual(
            expand("$(OUT)/x", {"OUT": "$(ROOT)/lib", "ROOT": "/tmp/tree"}),
            "/tmp/tree/lib/x",
        )

    def test_adjacent_references_concatenate(self):
        self.assertEqual(expand("$(A)$(B)", {"A": "ab", "B": "cd"}), "abcd")

    def test_unterminated_reference_is_an_error(self):
        with self.assertRaises(SmakeError):
            expand("$(NAME", {})


class MatchModifierTest(unittest.TestCase):
    def test_match_keeps_only_matching_words(self):
        self.assertEqual(
            expand("$(STYLES:M32*)", {"STYLES": "64_M3 32_M2 N32_M3"}), "32_M2"
        )

    def test_match_keeps_several_words(self):
        self.assertEqual(
            expand("$(STYLES:M*_M3)", {"STYLES": "64_M3 32_M2 64_M3"}), "64_M3 64_M3"
        )

    def test_match_question_mark_is_one_character(self):
        self.assertEqual(
            expand(
                "$(LIBRARY:M?*_ns.a)",
                {"LIBRARY": "libc_32_M2_ns.a libc_n32_M3_ns.a libc.a"},
            ),
            "libc_32_M2_ns.a libc_n32_M3_ns.a",
        )

    def test_match_that_matches_nothing_is_empty(self):
        self.assertEqual(
            expand("$(STYLE:M64*)", {"STYLE": "32_M2"}), ""
        )

    def test_not_modifier_drops_matching_words(self):
        self.assertEqual(
            expand("$(STYLE:M32*:N32_ABI)", {"STYLE": "32_M2"}), "32_M2"
        )

    def test_global_substitution(self):
        self.assertEqual(
            expand("$(FLAGS:S/-mips1/-mips2/)", {"FLAGS": "-mips1 -g"}), "-mips2 -g"
        )

    def test_chained_substitution(self):
        self.assertEqual(
            expand(
                "$(FLAGS:S/-mips1/-mips2/:S/-abi//)",
                {"FLAGS": "-mips1 -abi"},
            ),
            "-mips2 ",
        )

    def test_substitution_then_match(self):
        self.assertEqual(
            expand("$(S:S/32_M2/GOT_ONE/:MGOT_ONE)", {"S": "64_M3 32_M2"}),
            "GOT_ONE",
        )

    def test_substitution_of_an_expanded_variable(self):
        self.assertEqual(
            expand("$(F:S/$(R)//g)", {"F": "/usr/include/x", "R": "/usr"}), "/include/x"
        )

    def test_helpers_are_public(self):
        self.assertEqual(filter_match("a b c", "a*"), "a")
        self.assertEqual(substitute("a-b", "-", "_"), "a_b")


class ConditionTest(unittest.TestCase):
    def test_equality_selects_the_branch(self):
        text = "#if $(LIB) == \"libc.a\"\nA=yes\n#else\nA=no\n#endif\n"
        self.assertEqual(evaluate(text, {"LIB": "libc.a"})["A"], "yes")
        self.assertEqual(evaluate(text, {"LIB": "libm.a"})["A"], "no")

    def test_inequality_selects_the_branch(self):
        text = "#if $(VCC) != \"CFE\"\nA=modern\n#else\nA=cfe\n#endif\n"
        self.assertEqual(evaluate(text, {"VCC": "MIPSpro"})["A"], "modern")
        self.assertEqual(evaluate(text, {"VCC": "CFE"})["A"], "cfe")

    def test_match_condition_is_true_when_non_empty(self):
        text = "#if $(STYLE:M32*) != \"\"\nA=thirtytwo\n#endif\n"
        self.assertEqual(evaluate(text, {"STYLE": "32_M2"})["A"], "thirtytwo")
        self.assertNotIn("A", evaluate(text, {"STYLE": "N32_M3"}))

    def test_defined(self):
        text = "#if defined(FLAG)\nA=on\n#endif\n"
        self.assertEqual(evaluate(text, {"FLAG": ""})["A"], "on")
        self.assertNotIn("A", evaluate(text, {}))

    def test_not_defined(self):
        text = "#if !defined(FLAG)\nA=on\n#endif\n"
        self.assertEqual(evaluate(text, {})["A"], "on")
        self.assertNotIn("A", evaluate(text, {"FLAG": "x"}))

    def test_bare_non_empty_reference_is_true(self):
        text = "#if $(FLAG)\nA=on\n#endif\n"
        self.assertEqual(evaluate(text, {"FLAG": "x"})["A"], "on")
        self.assertNotIn("A", evaluate(text, {"FLAG": ""}))

    def test_and_or_precedence(self):
        text = (
            "#if $(A) == \"1\" && $(B) == \"2\" || $(C) == \"3\"\n"
            "R=yes\n#endif\n"
        )
        self.assertEqual(evaluate(text, {"A": "1", "B": "2", "C": "0"})["R"], "yes")
        self.assertEqual(evaluate(text, {"A": "0", "B": "0", "C": "3"})["R"], "yes")
        self.assertNotIn("R", evaluate(text, {"A": "1", "B": "0", "C": "0"}))

    def test_parenthesised_conditions(self):
        text = "#if ($(A) == \"1\" || $(B) == \"2\") && $(C) == \"3\"\nR=yes\n#endif\n"
        self.assertEqual(evaluate(text, {"A": "1", "B": "0", "C": "3"})["R"], "yes")
        self.assertNotIn("R", evaluate(text, {"A": "1", "B": "0", "C": "0"}))

    def test_elif_chain(self):
        text = (
            "#if $(S) == \"a\"\nR=one\n"
            "#elif $(S) == \"b\"\nR=two\n"
            "#else\nR=other\n#endif\n"
        )
        self.assertEqual(evaluate(text, {"S": "a"})["R"], "one")
        self.assertEqual(evaluate(text, {"S": "b"})["R"], "two")
        self.assertEqual(evaluate(text, {"S": "z"})["R"], "other")

    def test_nested_conditionals(self):
        text = (
            "#if $(OUTER) == \"1\"\n"
            "#if $(INNER) == \"1\"\nR=both\n"
            "#else\nR=outer\n#endif\n"
            "#else\nR=none\n#endif\n"
        )
        self.assertEqual(evaluate(text, {"OUTER": "1", "INNER": "1"})["R"], "both")
        self.assertEqual(evaluate(text, {"OUTER": "1", "INNER": "0"})["R"], "outer")
        self.assertEqual(evaluate(text, {"OUTER": "0", "INNER": "1"})["R"], "none")

    def test_unbalanced_conditional_is_an_error(self):
        with self.assertRaises(SmakeError):
            evaluate("#if 1\nA=yes\n", {})


class FileListTest(unittest.TestCase):
    def test_lists_accumulate_and_expand(self):
        text = (
            "ABI_CFILES = one.c two.c\n"
            "NONABI_CFILES = three.c\n"
            "#if $(LIBRARY:M?*_ns.a) != \"\"\n"
            "CFILES = $(ABI_CFILES) $(NONABI_CFILES)\n"
            "#else\n"
            "CFILES = $(ABI_CFILES)\n"
            "#endif\n"
            "ASFILES = asm.s\n"
        )
        variables = evaluate(text, {"LIBRARY": "libc_32_M2_ns.a"})
        self.assertEqual(
            variables["CFILES"].split(), ["one.c", "two.c", "three.c"]
        )
        self.assertEqual(variables["ASFILES"].split(), ["asm.s"])

    def test_append_builds_a_list(self):
        text = "CFILES = a.c\nCFILES += b.c\nCFILES += c.c\n"
        self.assertEqual(
            evaluate(text, {})["CFILES"].split(), ["a.c", "b.c", "c.c"]
        )

    def test_conditional_append(self):
        text = (
            "CFILES = a.c\n"
            "#if $(STYLE:M32*) != \"\"\n"
            "CFILES += b.c\n"
            "#endif\n"
        )
        self.assertEqual(evaluate(text, {"STYLE": "32_M2"})["CFILES"].split(), ["a.c", "b.c"])
        self.assertEqual(evaluate(text, {"STYLE": "N32_M3"})["CFILES"].split(), ["a.c"])

    def test_line_continuation(self):
        text = "CFILES = alpha.c \\\n\tbeta.c \\\n\tgamma.c\n"
        self.assertEqual(
            evaluate(text, {})["CFILES"].split(), ["alpha.c", "beta.c", "gamma.c"]
        )

    def test_conditional_directive_can_continue(self):
        text = (
            "#if $(STYLE:M32*) != \"\" && \\\n"
            "    $(LIBRARY:M?*_ns.a) != \"\"\n"
            "R=yes\n#endif\n"
        )
        self.assertEqual(
            evaluate(text, {"STYLE": "32_M2", "LIBRARY": "libc_32_M2_ns.a"})["R"],
            "yes",
        )


class IncludeTest(unittest.TestCase):
    def test_quoted_include_is_resolved_through_the_callback(self):
        text = "include \"subs.defs\"\nA=after\n"
        files = {"subs.defs": "B=from-include\n"}
        variables = evaluate(text, {}, include=lambda name: files.get(name))
        self.assertEqual(variables["B"], "from-include")
        self.assertEqual(variables["A"], "after")

    def test_angle_include_is_resolved_through_the_callback(self):
        text = "include <$(ROOT)/defs.mk>\nA=after\n"
        files = {"/tree/defs.mk": "B=sys\n"}
        variables = evaluate(
            text, {"ROOT": "/tree"}, include=lambda name: files.get(name)
        )
        self.assertEqual(variables["B"], "sys")

    def test_missing_include_is_skipped(self):
        text = "include <$(ROOT)/defs.mk>\nA=after\n"
        self.assertEqual(evaluate(text, {"ROOT": "/tree"})["A"], "after")

    def test_include_sees_current_variables(self):
        text = "ROOT=/tree\ninclude <$(ROOT)/defs.mk>\n"
        files = {"/tree/defs.mk": "B=$(ROOT)/x\n"}
        variables = evaluate(text, {}, include=lambda name: files.get(name))
        self.assertEqual(variables["B"], "/tree/x")


if __name__ == "__main__":
    unittest.main()
