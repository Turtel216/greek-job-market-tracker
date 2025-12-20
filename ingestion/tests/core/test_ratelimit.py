import random
import threading

from conftest import FakeClock

from jobtracker_ingest.core.ratelimit import HostRateLimiter


def make_limiter(clock: FakeClock, min_delay: float = 3.0, jitter: float = 1.0) -> HostRateLimiter:
    return HostRateLimiter(
        min_delay_seconds=min_delay,
        max_jitter_seconds=jitter,
        clock=clock,
        sleep=clock.sleep,
        rng=random.Random(42),
    )


def test_first_request_to_a_host_does_not_wait(clock: FakeClock) -> None:
    limiter = make_limiter(clock)
    with limiter.slot("example.org") as waited:
        assert waited == 0
    assert clock.sleeps == []


def test_second_request_waits_delay_plus_jitter(clock: FakeClock) -> None:
    limiter = make_limiter(clock)
    with limiter.slot("example.org"):
        pass
    with limiter.slot("example.org") as waited:
        pass
    assert 3.0 <= waited <= 4.0
    assert clock.sleeps == [waited]


def test_delay_counts_from_the_end_of_the_previous_request(clock: FakeClock) -> None:
    limiter = make_limiter(clock, jitter=0)
    with limiter.slot("example.org"):
        clock.now += 10  # a slow response
    with limiter.slot("example.org") as waited:
        pass
    assert waited == 3.0


def test_time_already_passed_counts_towards_the_delay(clock: FakeClock) -> None:
    limiter = make_limiter(clock, jitter=0)
    with limiter.slot("example.org"):
        pass
    clock.now += 2
    with limiter.slot("example.org") as waited:
        pass
    assert waited == 1.0

    clock.now += 5
    with limiter.slot("example.org") as waited:
        pass
    assert waited == 0


def test_hosts_are_limited_separately(clock: FakeClock) -> None:
    limiter = make_limiter(clock)
    with limiter.slot("a.example.org"):
        pass
    with limiter.slot("b.example.org") as waited:
        pass
    assert waited == 0


def test_registry_delay_below_the_floor_is_raised(clock: FakeClock) -> None:
    limiter = make_limiter(clock, min_delay=0.5, jitter=0)
    assert limiter.min_delay_seconds == 3.0


def test_registry_delay_above_the_floor_is_kept(clock: FakeClock) -> None:
    limiter = make_limiter(clock, min_delay=8, jitter=0)
    with limiter.slot("example.org"):
        pass
    with limiter.slot("example.org") as waited:
        pass
    assert waited == 8.0


def test_host_delay_overrides_but_never_goes_below_the_minimum(clock: FakeClock) -> None:
    limiter = make_limiter(clock, min_delay=5, jitter=0)
    limiter.set_host_delay("slow.example.org", 20)
    limiter.set_host_delay("fast.example.org", 1)
    assert limiter.delay_for("slow.example.org") == 20
    assert limiter.delay_for("fast.example.org") == 5
    assert limiter.delay_for("other.example.org") == 5


def test_only_one_request_per_host_at_a_time() -> None:
    limiter = HostRateLimiter(sleep=lambda _: None)
    inside = 0
    most_inside = 0
    counter_lock = threading.Lock()

    def worker() -> None:
        nonlocal inside, most_inside
        with limiter.slot("example.org"):
            with counter_lock:
                inside += 1
                most_inside = max(most_inside, inside)
            threading.Event().wait(0.01)
            with counter_lock:
                inside -= 1

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most_inside == 1
