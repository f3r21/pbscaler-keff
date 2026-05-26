# Alibaba cluster-trace-v2018 — derived data for Online Boutique trace_driven workload

## Files

| File | Status | Description |
|---|---|---|
| `prepare_alibaba_trace.py` | versioned | Reads raw `batch_task.csv`, picks the highest-mean 60-min window after trimming warm-up/cooldown, writes a 60-row curve. |
| `alibaba_60min_curve.csv` | versioned | Preprocessed 60-row arrival curve. Loaded by `../locustfile_trace_driven.py`. ~600 bytes. |
| `batch_task.csv` | gitignored (~800 MB) | Raw Alibaba dataset, extracted from tarball. |
| `batch_task.tar.gz` | gitignored (~125 MB) | Raw download. |

## Reproducing

```bash
cd code/benchmarks/online_boutique/data

# 1. Download (~125 MB, hosted by Alibaba on Beijing OSS)
curl -L --fail -o batch_task.tar.gz \
  http://aliopentrace.oss-cn-beijing.aliyuncs.com/v2018Traces/batch_task.tar.gz

# 2. Extract (~800 MB CSV, no header — schema below)
tar -xzf batch_task.tar.gz

# 3. Preprocess (~60-90 s)
python3 prepare_alibaba_trace.py
# → writes alibaba_60min_curve.csv
```

## batch_task.csv schema

No header. Column order from `cluster-trace-v2018/schema.txt`:

| # | Field | Type | Description |
|---|---|---|---|
| 1 | task_name | string | unique within job |
| 2 | instance_num | bigint | number of instances |
| 3 | job_name | string | |
| 4 | task_type | string | |
| 5 | status | string | |
| 6 | **start_time** | bigint | **seconds since trace epoch** (used here) |
| 7 | end_time | bigint | |
| 8 | plan_cpu | double | 100 = 1 core |
| 9 | plan_mem | double | 0..100 normalized |

## Window selection

`prepare_alibaba_trace.py` uses the following heuristic:

1. Bucket all `start_time` values into 60-second buckets.
2. Skip the first 60 buckets (warm-up artifact: bucket 0 contains all
   pre-existing tasks; buckets 1..~10 are essentially empty).
3. Skip the last 60 buckets (cooldown / drainage).
4. Among the remaining buckets, slide a 60-bucket window and pick the
   one with the highest **mean** arrivals — *not* peak, because
   single-spike windows misrepresent sustained load.

For the 2018 v1 trace this picks bucket 6939 (~115 h into the trace,
day 5). Stats of the chosen window:

- mean: 3843 arrivals/min
- peak: 5166
- min:  2396

The locustfile then linearly scales so that `peak → 3 * BASE_USERS = 600 users`.

## Provenance

- Project: https://github.com/alibaba/clusterdata
- Trace: cluster-trace-v2018, derived from production Alibaba clusters
- Citation: "Alibaba clusterdata-v2018: an 8-day production trace from
  4034 machines."
