/*
 * Oracle reference program (issue #3).
 *
 * Compiled natively inside the fresh IRIX 6.5.7m guest with MIPSpro (cc) to
 * give ground truth for the new toolchain: printf exercises libc, sqrt
 * exercises libm, and the output is fixed so two toolchains can be diffed.
 * Kept deliberately C89 so every MIPSpro/GCC generation accepts it.
 */
#include <stdio.h>
#include <math.h>

int main(void)
{
	printf("hello from MIPSpro\n");
	printf("sqrt(2) = %.6f\n", sqrt(2.0));
	return 0;
}
