/*
 * Maths-boundary smoke fixture (issue #5, audit C10).
 *
 * The oracle's hello.c calls sqrt(2.0): a constant the compiler may fold, so
 * that case does not establish that the dynamic libm boundary is crossed.
 * This program takes its input from argv, so the value is unknown at compile
 * time and each call must go out to the captured libm. The host-side proof
 * (scripts/test-maths-boundary.sh) asserts sin and sqrt stay undefined in the
 * -O2 object, carry external R_MIPS_CALL16 relocations, and that the linked
 * o32 and n32 binaries carry a NEEDED libm.so entry; the controlled guest
 * runs are recorded separately (docs/smoke.md, "The maths boundary").
 *
 * With no argument the input is 2.0, which is what smoke.sh runs and what
 * maths-boundary.expected records.
 */
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

int main(int argc, char **argv)
{
	double x = 2.0;

	if (argc > 1)
		x = atof(argv[1]);

	printf("sin(%.6f) = %.6f\n", x, sin(x));
	printf("sqrt(%.6f) = %.6f\n", x, sqrt(x));
	return 0;
}
