import pebble
import multiprocessing
import typing
import threading
import enum

import pebble.pool
ProcessFuture = pebble.ProcessFuture

_UserDataT = typing.TypeVar("_UserDataT")

class CounterContext:
    _count = -1     # so that first number is 0
    def __enter__(self):
        self._count += 1
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def get_count(self):
        return self._count

class ProcessState(enum.Enum):
    Pending     = enum.auto()
    Running     = enum.auto()
    Canceled    = enum.auto()
    Failed      = enum.auto()
    Completed   = enum.auto()

class ProcessWaiter(object):
    """Routes completion through to user callback."""
    def __init__(self, work_id: int, user_data: _UserDataT, done_callback: typing.Callable[[ProcessFuture, _UserDataT, int, ProcessState], None]):
        self.done_callback = done_callback
        self.work_id = work_id
        self.user_data = user_data

    def add_result(self, future: ProcessFuture):
        self._notify(future, ProcessState.Completed)

    def add_exception(self, future: ProcessFuture):
        self._notify(future, ProcessState.Failed)

    def add_cancelled(self, future: ProcessFuture):
        self._notify(future, ProcessState.Canceled)

    def _notify(self, future: ProcessFuture, state: ProcessState):
        self.done_callback(future, self.work_id, self.user_data, state)


class ProcessPool:
    def __init__(self, done_callback: typing.Callable[[ProcessFuture, _UserDataT, int, ProcessState], None] = None, num_workers = 2):
        self.done_callback          = done_callback
        self.num_workers            = num_workers
        self.auto_cleanup_if_no_work= False

        # NB: pool is only started in run() once needed
        self._pool              : pebble.pool.ProcessPool                   = None
        self._work_items        : dict[int,tuple[ProcessFuture, _UserDataT]]= None
        self._work_id_provider  : CounterContext                            = CounterContext()
        self._lock              : threading.Lock                            = threading.Lock()

    def _cleanup(self):
        # cancel all pending and running jobs
        self.cancel_all_jobs()

        # stop pool
        if self._pool and self._pool.active:
            self._pool.stop()
            self._pool.join()
        self._pool = None
        self._work_items = None

    def cleanup(self):
        with self._lock:
            self._cleanup()

    def cleanup_if_no_work(self):
        with self._lock:
            self._cleanup_if_no_work()

    def _cleanup_if_no_work(self):
        # NB: lock must be acquired when calling this
        if self._pool and not self._work_items:
            self._cleanup()

    def set_num_workers(self, num_workers: int):
        # NB: doesn't change number of workers on an active pool, only takes effect when pool is restarted
        self.num_workers = num_workers

    def run(self, fn: typing.Callable, user_data: _UserDataT=None, *args, **kwargs):
        with self._lock:
            if self._pool is None or not self._pool.active:
                context = multiprocessing.get_context("spawn")  # ensure consistent behavior on Windows (where this is default) and Unix (where fork is default, but that may bring complications)
                self._pool = pebble.ProcessPool(max_workers=self.num_workers, context=context)

            if self._work_items is None:
                self._work_items = {}

            with self._work_id_provider:
                work_id = self._work_id_provider.get_count()
                self._work_items[work_id] = (self._pool.schedule(fn, None, args=args, kwargs=kwargs), user_data)
                self._work_items[work_id][0]._waiters.append(ProcessWaiter(work_id, user_data, self._work_done_callback))
                if self.done_callback:
                    self._work_items[work_id][0]._waiters.append(ProcessWaiter(work_id, user_data, self.done_callback))
                return work_id

    def _work_done_callback(self, future: ProcessFuture, work_id: int, user_data: _UserDataT, state: ProcessState):
        with self._lock:
            if self._work_items is not None and work_id in self._work_items:
                # clean up the work item since we're done with it
                del self._work_items[work_id]

            if self.auto_cleanup_if_no_work:
                # close pool if no work left
                self._cleanup_if_no_work()

    def get_job_state(self, wid: int) -> ProcessState:
        if not self._work_items:
            return None
        work_item = self._work_items.get(wid, None)
        if work_item is None:
            return None
        else:
            return _get_status_from_future(work_item[0])

    def get_job_user_data(self, wid: int) -> _UserDataT:
        if not self._work_items:
            return None
        work_item = self._work_items.get(wid, None)
        if work_item is None:
            return None
        else:
            return work_item[1]

    def invoke_on_each_job(self, callback: typing.Callable[[ProcessFuture, _UserDataT], None]):
        with self._lock:
            for wid in self._work_items:
                callback(*self._work_items[wid])

    def cancel_job(self, wid: int) -> bool:
        if not self._work_items:
            return False
        if (future := self._work_items.get(wid, None)) is None:
            return False
        return future.cancel()

    def cancel_all_jobs(self):
        if not self._work_items:
            return
        for wid in reversed(self._work_items):    # reversed so that later pending jobs don't start executing when earlier gets cancelled, only to be canceled directly after
            if not self._work_items[wid].done():
                self._work_items[wid].cancel()


def _get_status_from_future(fut: ProcessFuture) -> ProcessState:
    if fut.running():
        return ProcessState.Running
    elif fut.done():
        if fut.cancelled():
            return ProcessState.Canceled
        elif fut.exception() is not None:
            return ProcessState.Failed
        else:
            return ProcessState.Completed
    else:
        return ProcessState.Pending