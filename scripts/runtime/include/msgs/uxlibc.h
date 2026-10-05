/*
 * Reconstruction of the build-generated <msgs/uxlibc.h> (issue #9).
 *
 * Same story as msgs/uxsgicore.h: the generator output is absent from the
 * upload and the dev install. These are the regex error message identifiers
 * regerror() looks up with gettxt(); the default text lives in regerror.c's
 * table, so the values are reconstruction (catalogue lookup only) and the
 * no-catalogue path returns the source's defaults. Names match the tree's
 * regerror.c table.
 */
#ifndef __MSGS_UXLIBC_H__
#define __MSGS_UXLIBC_H__

#define _SGI_regcomp_REG_NOMATCH	"uxlibc:501"
#define _SGI_regcomp_REG_BADPAT		"uxlibc:502"
#define _SGI_regcomp_REG_ECOLLATE	"uxlibc:503"
#define _SGI_regcomp_REG_ECTYPE		"uxlibc:504"
#define _SGI_regcomp_REG_EESCAPE	"uxlibc:505"
#define _SGI_regcomp_REG_ESUBREG	"uxlibc:506"
#define _SGI_regcomp_REG_EBRACK		"uxlibc:507"
#define _SGI_regcomp_REG_EPAREN		"uxlibc:508"
#define _SGI_regcomp_REG_EBRACE		"uxlibc:509"
#define _SGI_regcomp_REG_BADBR		"uxlibc:510"
#define _SGI_regcomp_REG_ERANGE		"uxlibc:511"
#define _SGI_regcomp_REG_ESPACE		"uxlibc:512"
#define _SGI_regcomp_REG_BADRPT		"uxlibc:513"
#define _SGI_regcomp_REG_EMPTY		"uxlibc:514"
#define _SGI_regcomp_REG_ASSERT		"uxlibc:515"
#define _SGI_regcomp_REG_INVARG		"uxlibc:516"
#define _SGI_regcomp_unknown		"uxlibc:517"

#define _SGI_Sregcomp_REG_NOMATCH	"uxlibc:101"
#define _SGI_Sregcomp_REG_BADPAT	"uxlibc:102"
#define _SGI_Sregcomp_REG_ECOLLATE	"uxlibc:103"
#define _SGI_Sregcomp_REG_ECTYPE	"uxlibc:104"
#define _SGI_Sregcomp_REG_EESCAPE	"uxlibc:105"
#define _SGI_Sregcomp_REG_ESUBREG	"uxlibc:106"
#define _SGI_Sregcomp_REG_EBRACK	"uxlibc:107"
#define _SGI_Sregcomp_REG_EPAREN	"uxlibc:108"
#define _SGI_Sregcomp_REG_EBRACE	"uxlibc:109"
#define _SGI_Sregcomp_REG_BADBR		"uxlibc:110"
#define _SGI_Sregcomp_REG_ERANGE	"uxlibc:111"
#define _SGI_Sregcomp_REG_ESPACE	"uxlibc:112"
#define _SGI_Sregcomp_REG_BADRPT	"uxlibc:113"
#define _SGI_Sregcomp_REG_EMPTY		"uxlibc:114"
#define _SGI_Sregcomp_REG_ASSERT	"uxlibc:115"
#define _SGI_Sregcomp_REG_INVARG	"uxlibc:116"
#define _SGI_Sregcomp_unknown		"uxlibc:117"

#endif /* __MSGS_UXLIBC_H__ */
