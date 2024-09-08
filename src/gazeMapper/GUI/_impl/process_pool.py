import pebble
import multiprocessing
import typing
import threading
import enum

import pebble.pool
ProcessFuture = pebble.ProcessFuture

from ... import process

_UserDataT = typing.TypeVar("_UserDataT")

class CounterContext:
    _count = -1     # so that first number is 0
    def __enter__(self):
        self._count += 1
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def get_count(self):
        return self._count

class ProcessWaiter(object):
    """Routes completion through to user callback."""
    def __init__(self, job_id: int, user_data: _UserDataT, done_callback: typing.Callable[[ProcessFuture, _UserDataT, int, process.State], None]):
        self.done_callback  = done_callback
        self.job_id         = job_id
        self.user_data      = user_data

    def add_result(self, future: ProcessFuture):
        self._notify(future, process.State.Completed)

    def add_exception(self, future: ProcessFuture):
        self._notify(future, process.State.Failed)

    def add_cancelled(self, future: ProcessFuture):
        self._notify(future, process.State.Canceled)

    def _notify(self, future: ProcessFuture, state: process.State):
        self.done_callback(future, self.job_id, self.user_data, state)


class ProcessPool:
    def __init__(self, done_callback: typing.Callable[[ProcessFuture, _UserDataT, int, process.State], None] = None, num_workers = 2):
        self.done_callback          = done_callback
        self.num_workers            = num_workers
        self.auto_cleanup_if_no_work= False

        # NB: pool is only started in run() once needed
        self._pool              : pebble.pool.ProcessPool                   = None
        self._jobs              : dict[int,tuple[ProcessFuture, _UserDataT]]= None
        self._job_id_provider   : CounterContext                            = CounterContext()
        self._lock              : threading.Lock                            = threading.Lock()

    def _cleanup(self):
        # cancel all pending and running jobs
        self.cancel_all_jobs()

        # stop pool
        if self._pool and self._pool.active:
            self._pool.stop()
            self._pool.join()
        self._pool = None
        self._jobs = None

    def cleanup(self):
        with self._lock:
            self._cleanup()

    def cleanup_if_no_jobs(self):
        with self._lock:
            self._cleanup_if_no_jobs()

    def _cleanup_if_no_jobs(self):
        # NB: lock must be acquired when calling this
        if self._pool and not self._jobs:
            self._cleanup()

    def set_num_workers(self, num_workers: int):
        # NB: doesn't change number of workers on an active pool, only takes effect when pool is restarted
        self.num_workers = num_workers

    def run(self, fn: typing.Callable, user_data: _UserDataT=None, *args, **kwargs):
        with self._lock:
            if self._pool is None or not self._pool.active:
                context = multiprocessing.get_context("spawn")  # ensure consistent behavior on Windows (where this is default) and Unix (where fork is default, but that may bring complications)
                self._pool = pebble.ProcessPool(max_workers=self.num_workers, context=context)

            if self._jobs is None:
                self._jobs = {}

            with self._job_id_provider:
                job_id = self._job_id_provider.get_count()
                self._jobs[job_id] = (self._pool.schedule(fn, args=args, kwargs=kwargs), user_data)
                self._jobs[job_id][0]._waiters.append(ProcessWaiter(job_id, user_data, self._job_done_callback))
                if self.done_callback:
                    self._jobs[job_id][0]._waiters.append(ProcessWaiter(job_id, user_data, self.done_callback))
                return job_id

    def _job_done_callback(self, future: ProcessFuture, job_id: int, user_data: _UserDataT, state: process.State):
        with self._lock:
            if self._jobs is not None and job_id in self._jobs:
                # clean up the work item since we're done with it
                del self._jobs[job_id]

            if self.auto_cleanup_if_no_work:
                # close pool if no work left
                self._cleanup_if_no_jobs()

    def get_job_state(self, job_id: int) -> process.State:
        if not self._jobs:
            return None
        job = self._jobs.get(job_id, None)
        if job is None:
            return None
        else:
            return _get_status_from_future(job[0])

    def get_job_user_data(self, job_id: int) -> _UserDataT:
        if not self._jobs:
            return None
        job = self._jobs.get(job_id, None)
        if job is None:
            return None
        else:
            return job[1]

    def invoke_on_each_job(self, callback: typing.Callable[[ProcessFuture, _UserDataT], None]):
        with self._lock:
            for job_id in self._jobs:
                callback(*self._jobs[job_id])

    def cancel_job(self, wid: int) -> bool:
        if not self._jobs:
            return False
        if (future := self._jobs.get(wid, None)) is None:
            return False
        return future.cancel()

    def cancel_all_jobs(self):
        if not self._jobs:
            return
        for job_id in reversed(self._jobs): # reversed so that later pending jobs don't start executing when earlier gets cancelled, only to be canceled directly after
            if not self._jobs[job_id].done():
                self._jobs[job_id].cancel()


def _get_status_from_future(fut: ProcessFuture) -> process.State:
    if fut.running():
        return process.State.Running
    elif fut.done():
        if fut.cancelled():
            return process.State.Canceled
        elif fut.exception() is not None:
            return process.State.Failed
        else:
            return process.State.Completed
    else:
        return process.State.Pending