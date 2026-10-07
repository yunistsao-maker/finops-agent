import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def group(service, usage_type, cost, usage=1.0, unit="Units"):
    return {"Keys": [service, usage_type],
            "Metrics": {"UnblendedCost": {"Amount": str(cost), "Unit": "USD"},
                        "UsageQuantity": {"Amount": str(usage), "Unit": unit}}}


class FakeCE:
    """Answers GetCostAndUsage from {day: [group, ...]}; pages after ``page_size`` days to exercise pagination."""

    def __init__(self, days, tags=(), tag_days=None, page_size=3, estimated=()):
        self.days, self.tags, self.tag_days = days, list(tags), tag_days or {}
        self.page_size, self.estimated = page_size, set(estimated)
        self.calls = []

    def get_cost_and_usage(self, **kw):
        self.calls.append(("get_cost_and_usage", kw))
        start, end = kw["TimePeriod"]["Start"], kw["TimePeriod"]["End"]
        by_tag = kw["GroupBy"][0]["Type"] in ("TAG", "COST_CATEGORY")
        source = self.tag_days if by_tag else self.days
        all_days = sorted(d for d in self._range(start, end))
        offset = int(kw.get("NextPageToken") or 0)
        page = all_days[offset:offset + self.page_size]
        results = [{"TimePeriod": {"Start": d}, "Groups": source.get(d, []), "Estimated": d in self.estimated} for d in page]
        resp = {"ResultsByTime": results}
        if offset + self.page_size < len(all_days):
            resp["NextPageToken"] = str(offset + self.page_size)
        return resp

    def get_tags(self, **kw):
        self.calls.append(("get_tags", kw))
        return {"Tags": self.tags}

    @staticmethod
    def _range(start, end):
        from datetime import date, timedelta
        d, e = date.fromisoformat(start), date.fromisoformat(end)
        while d < e:
            yield d.isoformat()
            d += timedelta(days=1)


class FakeCW:
    def __init__(self, usage):
        """usage: {(metric, arn): {day: value}}"""
        self.usage = usage

    def list_metrics(self, **kw):
        out = []
        for metric, arn in self.usage:
            if metric == kw["MetricName"]:
                out.append({"Dimensions": [{"Name": "Resource", "Value": arn}, {"Name": "Service", "Value": "AgentCore.Runtime"}]})
                out.append({"Dimensions": [{"Name": "Service", "Value": "AgentCore.Runtime"}]})
        return {"Metrics": out}

    def get_metric_data(self, **kw):
        results = []
        for q in kw["MetricDataQueries"]:
            m = q["MetricStat"]["Metric"]
            arn = next(d["Value"] for d in m["Dimensions"] if d["Name"] == "Resource")
            series = self.usage.get((m["MetricName"], arn), {})
            results.append({"Id": q["Id"],
                            "Timestamps": [datetime.fromisoformat(d).replace(tzinfo=timezone.utc) for d in series],
                            "Values": list(series.values())})
        return {"MetricDataResults": results}


class FakeTagging:
    def __init__(self, tags):
        self.tags = tags

    def get_resources(self, **kw):
        return {"ResourceTagMappingList": [{"ResourceARN": a, "Tags": [{"Key": k, "Value": v} for k, v in t.items()]}
                                           for a, t in self.tags.items()]}


@pytest.fixture
def store(tmp_path):
    from finops.store import Store
    return Store(str(tmp_path / "data"))
