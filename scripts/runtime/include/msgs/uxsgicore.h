/*
 * Reconstruction of the build-generated <msgs/uxsgicore.h> (issue #9).
 *
 * The message-system headers are generated during an IRIX build and are not
 * present in the 6.5.7m source upload or the installed dev headers, so this
 * file re-supplies the names the libc sources use in the same shape the
 * generator emitted: string message identifiers passed to gettxt(), whose
 * second argument is the source's own default text. The identifiers only
 * drive catalogue lookup; with no catalogue present at run time gettxt()
 * returns the default, so the numbers below are reconstruction, not SGI
 * text. Only names actually referenced by the tree's libc sources appear.
 */
#ifndef __MSGS_UXSGICORE_H__
#define __MSGS_UXSGICORE_H__

#define _SGI_MMX_fmtmsg_HALT	"uxsgicore:1"
#define _SGI_MMX_fmtmsg_ERROR	"uxsgicore:2"
#define _SGI_MMX_fmtmsg_WARN	"uxsgicore:3"
#define _SGI_MMX_fmtmsg_INFO	"uxsgicore:4"
#define _SGI_MMX_fmtmsg_REASON	"uxsgicore:5"
#define _SGI_MMX_fmtmsg_FIX	"uxsgicore:6"
#define _SGI_MMX_fmtmsg_dtext	"uxsgicore:7"
#define _SGI_MMX_fmtmsg_dsep	"uxsgicore:8"
#define _SGI_MMX_Usage		"uxsgicore:9"
#define _SGI_MMX_usagespc	"uxsgicore:10"

#endif /* __MSGS_UXSGICORE_H__ */
