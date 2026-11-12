"""Standard Sol token accounting, including explicit cache writes."""


def usage_cost(usage):
    inp, out = usage['input_tokens'], usage['output_tokens']
    details = usage.get('input_tokens_details') or {}
    reads = details.get('cached_tokens', 0)
    writes = details.get('cache_write_tokens', 0)
    lifetimes = details.get('cache_write_tokens_details') or {}
    short = lifetimes.get('ttl_30m_tokens', writes)
    long = lifetimes.get('ttl_12h_tokens', 0)
    counts = (inp, out, reads, writes, short, long)
    if not all(type(n) is int and n >= 0 for n in counts):
        raise ValueError('Invalid provider usage')
    if reads + writes > inp or short + long != writes:
        return (inp * 5 + out * 10) / 1_000_000
    return ((inp - reads - writes) * 2 + reads * .10 + short * 2.50 + long * 5 + out * 10) / 1_000_000
