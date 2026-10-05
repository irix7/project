/*
 * stdint width and limit probe for the MIPSpro oracle comparison (issue #30).
 *
 * Independently authored: this file is compiled twice for each 32-bit ABI —
 * once by the GCC 16.2 cross against the captured sysroot and once by the
 * guest's native MIPSpro cc — and the two programmes' stdout is diffed. It
 * names only standard headers, types and macros, so the comparison reads
 * whatever integer model each compiler actually provides rather than any
 * captured header text.
 *
 * The cross finds GCC's provided <stdint.h>; the captured guest has no
 * <stdint.h> at all, so the native side falls back to the capture's
 * <inttypes.h> through the __has_include guard (old preprocessors without
 * the extension simply skip the inner block). The one C99 item the capture
 * lacks is SIZE_MAX, so the probe supplies the standard definition itself.
 * UINT64_MAX is printed as two 32-bit hexadecimal halves to stay within C89
 * printf conversions. Both o32 and n32 are ILP32, so the expected output is
 * the same for both.
 *
 * Kept C89 so MIPSpro 7.3 accepts it.
 */
#include <stdio.h>
#include <inttypes.h>

#if defined(__has_include)
#  if __has_include(<stdint.h>)
#include <stdint.h>
#  endif
#endif

#ifndef SIZE_MAX
#define SIZE_MAX ((size_t)-1)
#endif

int main(void)
{
	uint64_t wide;

	printf("sizeof(int8_t)=%d\n", (int)sizeof(int8_t));
	printf("sizeof(int16_t)=%d\n", (int)sizeof(int16_t));
	printf("sizeof(int32_t)=%d\n", (int)sizeof(int32_t));
	printf("sizeof(int64_t)=%d\n", (int)sizeof(int64_t));
	printf("sizeof(uint8_t)=%d\n", (int)sizeof(uint8_t));
	printf("sizeof(uint16_t)=%d\n", (int)sizeof(uint16_t));
	printf("sizeof(uint32_t)=%d\n", (int)sizeof(uint32_t));
	printf("sizeof(uint64_t)=%d\n", (int)sizeof(uint64_t));
	printf("sizeof(intptr_t)=%d\n", (int)sizeof(intptr_t));
	printf("sizeof(uintptr_t)=%d\n", (int)sizeof(uintptr_t));
	printf("sizeof(intmax_t)=%d\n", (int)sizeof(intmax_t));
	printf("sizeof(uintmax_t)=%d\n", (int)sizeof(uintmax_t));
	printf("sizeof(void*)=%d\n", (int)sizeof(void *));
	printf("INT32_MAX=%ld\n", (long)INT32_MAX);
	wide = (uint64_t)UINT64_MAX;
	printf("UINT64_MAX=%08lx%08lx\n",
	       (unsigned long)(wide >> 32), (unsigned long)(wide & 0xffffffffUL));
	printf("SIZE_MAX=%lu\n", (unsigned long)SIZE_MAX);
	return 0;
}
