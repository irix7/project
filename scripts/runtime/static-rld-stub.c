/*
 * Static-link scaffolding for rld-provided symbols (issue #9).
 *
 * IRIX's dynamically linked libc takes three symbols from the runtime
 * linker: the loaded image's ELF header and program header table and rld's
 * displacement. A static binary has no rld, so a zeroed ELF header makes
 * libcthread's locate_dso() walk zero program headers and return ESRCH:
 * the correct answer for "no DSOs exist in this process". The lock and
 * thread entry points that libcthread.o carries still resolve, so the
 * nonshared archive links without a dynamic loader.
 *
 * This file is the project's own, not tree material (ADR-0001).
 */

unsigned int __elf_header[16];
unsigned int __program_header_table[1];
unsigned int __dso_displacement;
