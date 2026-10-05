/* Runtime proof for the common-symbol alignment contract (issue #28).
   Compile with -O2 -fcommon (the bug path) and run on the guest: the
   64-byte-aligned tentative definition must live where the compiler's
   optimised assumption says it does, and the ordinary one keeps at least
   its ABI's natural alignment. */

#include <stdio.h>

int ordinary_slot[8];
__attribute__((aligned(64))) char aligned_slot[64];

int main(void)
{
	unsigned long ordinary_rem = (unsigned long) ordinary_slot % 4;
	unsigned long aligned_rem = (unsigned long) aligned_slot % 64;

	printf("ordinary_rem=%lu aligned_rem=%lu\n", ordinary_rem, aligned_rem);
	return ordinary_rem != 0 || aligned_rem != 0;
}
