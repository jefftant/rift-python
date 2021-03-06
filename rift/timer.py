from datetime import datetime
from sortedcontainers import SortedDict

class TimerScheduler:

    def __init__(self):
        self._epoch = datetime.now()
        self._timers_by_expire_time = SortedDict()

    def now(self):
        absolute_now = datetime.now()
        time_since_epoch = absolute_now - self._epoch
        return time_since_epoch.total_seconds()

    def schedule(self, timer):
        expire_time = timer.expire_time()
        assert expire_time is not None
        if expire_time in self._timers_by_expire_time:
            self._timers_by_expire_time[expire_time].append(timer)
        else:
            self._timers_by_expire_time[expire_time] = [timer]

    def unschedule(self, timer):
        expire_time = timer.expire_time()
        assert expire_time is not None
        assert expire_time in self._timers_by_expire_time
        timers_with_matching_expire = self._timers_by_expire_time[expire_time]
        assert timer in timers_with_matching_expire
        timers_with_matching_expire.remove(timer)
        if timers_with_matching_expire == []:
            self._timers_by_expire_time.pop(expire_time)

    def trigger_all_expired_timers(self):
        # Trigger all expired timers and return time until next expire
        now = self.now()
        while True:
            if not self._timers_by_expire_time:
                return None
            next_expire_time = self._timers_by_expire_time.peekitem(0)[0]
            if next_expire_time > now:
                return next_expire_time - now
            expired_timers = self._timers_by_expire_time.popitem(0)[1]
            for timer in expired_timers:
                timer.trigger_expire()

TIMER_SCHEDULER = TimerScheduler()

class Timer:

    def __init__(self, interval, expire_function, periodic=True, start=True):
        self._running = False
        self._periodic = periodic
        self._interval = interval
        self._expire_time = None
        self._expire_function = expire_function
        if start:
            self.start()

    def running(self):
        return self._running

    def interval(self):
        return self._interval

    def expire_time(self):
        return self._expire_time

    def remaining_time_str(self):
        if self._running:
            secs_left = self._expire_time - TIMER_SCHEDULER.now()
            return "{:06f} secs".format(secs_left)
        else:
            return "Stopped"

    def _update_expire_time(self):
        self._expire_time = TIMER_SCHEDULER.now() + self._interval

    def start(self):
        if self._running:
            self.stop()
        self._running = True
        self._update_expire_time()
        TIMER_SCHEDULER.schedule(self)

    def stop(self):
        if self._running:
            TIMER_SCHEDULER.unschedule(self)
            self._running = False
            self._expire_time = None

    def trigger_expire(self):
        self._expire_function()
        if self._periodic:
            self._update_expire_time()
            TIMER_SCHEDULER.schedule(self)
        else:
            self._running = False
            self._expire_time = None
