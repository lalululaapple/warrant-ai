from pathlib import Path
import unittest

import pandas as pd

from backend.filter import (
    backup_filter_mask,
    coarse_filter_counts,
    coarse_filter_mask,
    filter_backup_warrants,
    filter_warrants,
)
from backend.parser import normalize_dataframe
from backend.score import score_dataframe


FIXTURE = Path(__file__).parent / "fixtures" / "filter_cases.csv"


class FilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = pd.read_csv(FIXTURE)

    def test_all_fixed_filter_cases(self):
        actual = coarse_filter_mask(self.cases).astype(int).tolist()
        expected = self.cases["expected_pass"].astype(int).tolist()
        self.assertEqual(actual, expected)

    def test_filtered_codes_are_exact(self):
        result = filter_warrants(self.cases)
        self.assertEqual(result["warrant_code"].tolist(), ["V001", "V002", "V003"])

    def test_missing_greeks_and_iv_are_retained_when_price_is_valid(self):
        row = self.cases.iloc[[0]].copy()
        row[["delta", "theta", "bid_iv", "ask_iv"]] = float("nan")
        self.assertTrue(bool(coarse_filter_mask(row).iloc[0]))

    def test_filter_counts_are_cumulative_and_match_final_result(self):
        counts = coarse_filter_counts(self.cases)
        ordered = [
            counts[name] for name in
            ("raw", "moneyness", "price", "ratio", "days", "spread", "leverage", "final")
        ]
        self.assertEqual(ordered, sorted(ordered, reverse=True))
        self.assertEqual(counts["final"], len(filter_warrants(self.cases)))

    def test_backup_only_relaxes_spread_and_leverage(self):
        result = filter_backup_warrants(self.cases)
        self.assertEqual(
            result["warrant_code"].tolist(),
            ["F006", "F007", "F008"],
        )

    def test_backup_still_requires_positive_price(self):
        no_price = self.cases.iloc[[0]].copy()
        no_price["price"] = float("nan")
        self.assertFalse(bool(backup_filter_mask(no_price).iloc[0]))


class ParserTests(unittest.TestCase):
    def test_chinese_columns_with_spaces_are_normalized(self):
        raw = pd.DataFrame({
            "權證 代碼": ["123456"], "權證 名稱": ["測試權證"],
            "行使 比例": ["0.0200"], "剩餘 天數": ["120"],
            "價內外": ["5%價外"], "買賣 價差比%": ["1.0"],
            "實質 槓桿": ["2.5"], "DELTA": ["0.012"],
            "THETA": ["-0.01"], "買價 隱波%": ["80"],
            "賣價 隱波%": ["81"],
        })
        result = normalize_dataframe(raw)
        required = {
            "warrant_code", "warrant_name", "ratio", "days", "moneyness",
            "moneyness_pct", "spread", "leverage", "delta", "theta",
            "bid_iv", "ask_iv",
        }
        self.assertTrue(required.issubset(result.columns))


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame([
            {
                "warrant_code": "A", "price": 2.0, "volume": 1000,
                "ratio": 0.02, "days": 120, "moneyness_pct": 5,
                "spread": 1.0, "leverage": 2.5, "delta": 0.012,
                "theta": -0.01, "bid_iv": 80.0, "ask_iv": 81.0,
            },
            {
                "warrant_code": "B", "price": 1.0, "volume": 1000,
                "ratio": 0.05, "days": 120, "moneyness_pct": 5,
                "spread": 1.0, "leverage": 2.5, "delta": 0.03,
                "theta": -0.01, "bid_iv": 90.0, "ask_iv": 92.0,
            },
        ])

    def test_delta_and_theta_normalization(self):
        result = score_dataframe(self.rows).set_index("warrant_code")
        self.assertAlmostEqual(result.loc["A", "normalized_delta"], 0.60)
        self.assertAlmostEqual(result.loc["B", "normalized_delta"], 0.60)
        self.assertAlmostEqual(result.loc["A", "theta_decay_pct"], 0.50)
        self.assertAlmostEqual(result.loc["B", "theta_decay_pct"], 1.00)

    def test_lower_theta_and_iv_rank_better(self):
        result = score_dataframe(self.rows).set_index("warrant_code")
        self.assertGreater(result.loc["A", "score_theta"], result.loc["B", "score_theta"])
        self.assertGreater(result.loc["A", "score_iv"], result.loc["B", "score_iv"])

    def test_total_equals_visible_components(self):
        result = score_dataframe(self.rows)
        component_columns = [
            "score_delta", "score_theta", "score_iv", "score_leverage",
            "score_days", "score_spread", "score_volume", "score_moneyness",
        ]
        expected = result[component_columns].sum(axis=1).round(2)
        pd.testing.assert_series_equal(result["score"], expected, check_names=False)

    def test_missing_delta_gets_zero_delta_points(self):
        rows = self.rows.copy()
        rows.loc[0, "delta"] = float("nan")
        result = score_dataframe(rows).set_index("warrant_code")
        self.assertEqual(result.loc["A", "score_delta"], 0)


if __name__ == "__main__":
    unittest.main()

