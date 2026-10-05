/*
 * Recursive-mutex and helper contract probe (issue #31).
 *
 * An independently authored check of the IRIX pthread interfaces libgcc's
 * gthr-posix selection relies on: recursive mutex creation through
 * pthread_mutexattr_settype, a double lock and double unlock on one thread,
 * cross-thread exclusion, pthread_equal, sched_yield and the rwlock
 * contract. Built and run by scripts/test-gthread-recursive.sh for o32 and
 * n32; no SGI material is part of it.
 *
 * The program is self-checking: every named line must read "ok" (or the
 * symbolic result the contract names) and the final line must be PASS.
 * A missing pthread_equal entry point fails explicitly rather than letting
 * a weak reference call through a null pointer.
 */
#include <pthread.h>
#include <sched.h>
#include <errno.h>
#include <stdio.h>

static __typeof__ (pthread_equal) pthread_equal_ref
	__attribute__ ((__weakref__ ("pthread_equal"),
			__copy__ (pthread_equal)));

#define SPIN_LIMIT 1000000

static int failures;

static void
check (const char *name, int ok)
{
	printf ("%s=%s\n", name, ok ? "ok" : "FAIL");
	if (!ok)
		failures++;
}

static const char *
result_name (int rc)
{
	switch (rc)
	{
		case 0:
			return "ok";
		case EBUSY:
			return "EBUSY";
		case EINVAL:
			return "EINVAL";
		case EPERM:
			return "EPERM";
		default:
			return "other";
	}
}

static void
check_result (const char *name, int rc, int want)
{
	if (rc == want)
	{
		printf ("%s=%s\n", name, result_name (rc));
		return;
	}
	printf ("%s=%s\n", name, result_name (rc));
	failures++;
}

struct mutex_worker
{
	pthread_mutex_t *mutex;
	pthread_t main_id;
	volatile int first_trylock;
	volatile int final_trylock;
	int equal_main;
};

static void *
mutex_worker_main (void *arg)
{
	struct mutex_worker *w = arg;
	int i;

	w->equal_main = pthread_equal (w->main_id, pthread_self ());
	w->first_trylock = pthread_mutex_trylock (w->mutex);
	w->final_trylock = -1;
	for (i = 0; i < SPIN_LIMIT; i++)
	{
		int rc = pthread_mutex_trylock (w->mutex);
		if (rc == 0)
		{
			w->final_trylock = 0;
			pthread_mutex_unlock (w->mutex);
			break;
		}
		if (rc != EBUSY)
		{
			w->final_trylock = rc;
			break;
		}
		sched_yield ();
	}
	return NULL;
}

struct rw_worker
{
	pthread_rwlock_t *rwlock;
	volatile int tryrdlock;
};

static void *
rw_worker_main (void *arg)
{
	struct rw_worker *w = arg;
	w->tryrdlock = pthread_rwlock_tryrdlock (w->rwlock);
	return NULL;
}

int
main (void)
{
	pthread_mutexattr_t attr;
	pthread_mutex_t recursive;
	pthread_rwlock_t rwlock;
	pthread_t self = pthread_self ();
	struct mutex_worker mw;
	struct rw_worker rw;
	pthread_t worker;
	int rc, i;
	int type = -1;
	int (*equal_fn) (pthread_t, pthread_t);

	printf ("gthread-recursive: begin\n");

	check_result ("mutexattr_init",
		      pthread_mutexattr_init (&attr), 0);
	check_result ("mutexattr_settype_recursive",
		      pthread_mutexattr_settype (&attr,
						 PTHREAD_MUTEX_RECURSIVE), 0);
	rc = pthread_mutexattr_gettype (&attr, &type);
	check ("mutexattr_gettype_recursive",
	       rc == 0 && type == PTHREAD_MUTEX_RECURSIVE);
	check_result ("mutexattr_settype_invalid",
		      pthread_mutexattr_settype (&attr, 99), EINVAL);

	check_result ("recursive_init",
		      pthread_mutex_init (&recursive, &attr), 0);
	check_result ("mutexattr_destroy",
		      pthread_mutexattr_destroy (&attr), 0);

	check_result ("recursive_lock_1", pthread_mutex_lock (&recursive), 0);
	check_result ("recursive_lock_2", pthread_mutex_lock (&recursive), 0);
	check_result ("recursive_unlock_2", pthread_mutex_unlock (&recursive), 0);
	check_result ("recursive_unlock_1", pthread_mutex_unlock (&recursive), 0);

	check_result ("recursive_trylock_1",
		      pthread_mutex_trylock (&recursive), 0);
	check_result ("recursive_trylock_2",
		      pthread_mutex_trylock (&recursive), 0);
	check_result ("recursive_trylock_unlock_2",
		      pthread_mutex_unlock (&recursive), 0);
	check_result ("recursive_trylock_unlock_1",
		      pthread_mutex_unlock (&recursive), 0);

	mw.mutex = &recursive;
	mw.main_id = self;
	mw.first_trylock = -1;
	mw.final_trylock = -1;
	mw.equal_main = -1;
	check_result ("recursive_lock_for_cross_thread",
		      pthread_mutex_lock (&recursive), 0);
	check_result ("cross_thread_create",
		      pthread_create (&worker, NULL, mutex_worker_main, &mw), 0);
	for (i = 0; i < SPIN_LIMIT && mw.first_trylock == -1; i++)
		sched_yield ();
	check_result ("cross_thread_trylock_while_held",
		      mw.first_trylock, EBUSY);
	check_result ("recursive_unlock_for_cross_thread",
		      pthread_mutex_unlock (&recursive), 0);
	check_result ("cross_thread_join", pthread_join (worker, NULL), 0);
	check_result ("cross_thread_lock_after_release",
		      mw.final_trylock, 0);
	check ("pthread_equal_self", pthread_equal (self, self) != 0);
	check ("pthread_equal_other", mw.equal_main == 0);
	equal_fn = pthread_equal_ref;
	check ("pthread_equal_symbol",
	       equal_fn != 0 && equal_fn (self, self) != 0);
	check_result ("sched_yield", sched_yield (), 0);

	check_result ("recursive_destroy", pthread_mutex_destroy (&recursive), 0);

	check_result ("rwlock_init", pthread_rwlock_init (&rwlock, NULL), 0);
	check_result ("rwlock_trywrlock", pthread_rwlock_trywrlock (&rwlock), 0);
	rw.rwlock = &rwlock;
	rw.tryrdlock = -1;
	check_result ("rwlock_create",
		      pthread_create (&worker, NULL, rw_worker_main, &rw), 0);
	check_result ("rwlock_join", pthread_join (worker, NULL), 0);
	check_result ("rwlock_cross_tryrdlock", rw.tryrdlock, EBUSY);
	check_result ("rwlock_unlock", pthread_rwlock_unlock (&rwlock), 0);
	check_result ("rwlock_tryrdlock", pthread_rwlock_tryrdlock (&rwlock), 0);
	check_result ("rwlock_unlock_rd", pthread_rwlock_unlock (&rwlock), 0);
	check_result ("rwlock_destroy", pthread_rwlock_destroy (&rwlock), 0);

	if (failures != 0)
	{
		printf ("gthread-recursive: FAIL (%d)\n", failures);
		return 1;
	}
	printf ("gthread-recursive: PASS\n");
	return 0;
}
