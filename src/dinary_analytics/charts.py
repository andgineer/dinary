"""Reusable chart builders for the analytics dashboard."""

from dataclasses import dataclass

import altair as alt
import polars as pl


@dataclass
class ChartSize:
    width: int = 700
    height: int = 400
    year_width: int = 160


_DEFAULT_CHART_SIZE = ChartSize()


def make_chart_pair(
    expense_monthly_df: pl.DataFrame,
    income_monthly_df: pl.DataFrame,
    year_df: pl.DataFrame,
    total_income: float,
    category_order: list[str],
    period_title: str | None = None,
    size: ChartSize = _DEFAULT_CHART_SIZE,
) -> alt.HConcatChart:
    """Return hconcat(stacked_area + saved_line, year_bars + saved_text)."""
    total_expenses = float(year_df["total"].sum())
    annual_saved = total_income - total_expenses
    sign = "+" if annual_saved >= 0 else ""
    savings_color = "#2ca02c" if annual_saved >= 0 else "#d62728"

    monthly_totals = (
        expense_monthly_df.group_by("month").agg(pl.sum("total").alias("expenses")).sort("month")
    )
    savings_df = (
        income_monthly_df.join(monthly_totals, on="month", how="left")
        .fill_null(0)
        .sort("month")
        .with_columns((pl.col("income") - pl.col("expenses")).alias("saved"))
    )
    first_month = expense_monthly_df["month"].min()
    label_df = (
        expense_monthly_df.filter(pl.col("month") == first_month)
        .sort("cat_rank")
        .with_columns(pl.col("total").cum_sum().alias("y_top"))
        .with_columns((pl.col("y_top") - pl.col("total") / 2).alias("y_mid"))
    )

    color_scale = alt.Scale(domain=category_order, scheme="tableau20")
    areas = (
        alt.Chart(expense_monthly_df)
        .mark_area(opacity=0.85, interpolate="monotone")
        .encode(
            x=alt.X("month:O", title=None),
            y=alt.Y(
                "total:Q",
                stack=True,
                title=None,
                scale=alt.Scale(nice=True),
                axis=alt.Axis(tickCount=6, format="~s", grid=True),
            ),
            color=alt.Color("category:N", scale=color_scale, legend=None),
            order=alt.Order("cat_rank:Q", sort="ascending"),
            tooltip=["month:O", "category:N", alt.Tooltip("total:Q", format=".0f")],
        )
    )
    labels_enc = {"x": alt.X("month:O"), "y": alt.Y("y_mid:Q"), "text": alt.Text("category:N")}
    labels_bg = (
        alt.Chart(label_df)
        .mark_text(
            align="left",
            dx=5,
            fontSize=11,
            fontWeight="bold",
            fill="white",
            stroke="white",
            strokeWidth=5,
        )
        .encode(**labels_enc)
    )
    labels_fg = (
        alt.Chart(label_df)
        .mark_text(align="left", dx=5, fontSize=11, fontWeight="bold", fill="#333333")
        .encode(**labels_enc)
    )
    savings_line = (
        alt.Chart(savings_df)
        .mark_line(
            color="#555555",
            strokeWidth=2,
            strokeDash=[6, 3],
            point={"color": "#555555", "size": 40, "filled": True},
        )
        .encode(
            x=alt.X("month:O"),
            y=alt.Y("saved:Q", title="Saved (EUR)", axis=alt.Axis(orient="right")),
            tooltip=["month:O", alt.Tooltip("saved:Q", format=".0f", title="Saved")],
        )
    )
    savings_label = (
        alt.Chart(savings_df.tail(1))
        .mark_text(align="right", dx=-6, dy=-10, fontSize=10, fontWeight="bold")
        .encode(
            x=alt.X("month:O"),
            y=alt.Y("saved:Q"),
            text=alt.value("saved"),
            color=alt.value("#555555"),
        )
    )

    year_order = list(reversed(category_order))
    year_df_pos = year_df.with_columns(pl.lit(0.0).alias("label_x"))
    year_bars = (
        alt.Chart(year_df_pos)
        .mark_bar()
        .encode(
            y=alt.Y("category:N", sort=year_order, title=None, axis=None),
            x=alt.X("total:Q", title=None, axis=alt.Axis(tickCount=3, format="~s")),
            color=alt.Color("category:N", scale=color_scale, legend=None),
            tooltip=["category:N", alt.Tooltip("total:Q", format=".0f")],
        )
    )
    year_enc = {
        "y": alt.Y("category:N", sort=year_order, axis=None),
        "x": alt.X("label_x:Q"),
        "text": alt.Text("category:N"),
    }
    year_text_bg = (
        alt.Chart(year_df_pos)
        .mark_text(align="left", dx=5, fontSize=10, fill="white", stroke="white", strokeWidth=4)
        .encode(**year_enc)
    )
    year_text_fg = (
        alt.Chart(year_df_pos)
        .mark_text(align="left", dx=5, fontSize=10, fill="#333333")
        .encode(**year_enc)
    )
    saved_text = (
        alt.Chart(pl.DataFrame({"t": [f"Saved {sign}{annual_saved:,.0f}€"]}))
        .mark_text(fontSize=13, fontWeight="bold", align="center", baseline="middle")
        .encode(
            x=alt.value(size.year_width / 2),
            y=alt.value(15),
            text="t:N",
            color=alt.value(savings_color),
        )
        .properties(width=size.year_width, height=30)
    )

    title_kwargs: dict[str, str] = {"title": period_title} if period_title else {}
    return alt.hconcat(
        alt.layer(
            alt.layer(areas, labels_bg, labels_fg),
            alt.layer(savings_line, savings_label),
        )
        .resolve_scale(y="independent")
        .properties(width=size.width, height=size.height, **title_kwargs)
        .interactive(),
        alt.vconcat(
            alt.layer(year_bars, year_text_bg, year_text_fg).properties(
                width=size.year_width,
                height=size.height,
                title="Year total",
            ),
            saved_text,
            spacing=4,
        ),
        spacing=10,
    )


def make_event_chart(
    expense_df: pl.DataFrame,
    title: str,
    size: int = 280,
) -> alt.FacetChart | alt.LayerChart:
    """Return a donut chart of expense breakdown by category for an event.

    Categories are derived from the event's own expenses (top 9 + Other).
    Labels are rendered directly on segments; no separate legend.
    """
    _sorted = expense_df.sort("total", descending=True)
    _top = _sorted.head(9)
    _other_total = float(_sorted.slice(9)["total"].sum())
    if _other_total > 0:
        _chart_df = pl.concat(
            [_top, pl.DataFrame({"category": ["Other"], "total": [_other_total]})],
        )
    else:
        _chart_df = _top

    total = float(_chart_df["total"].sum())
    inner_r = size // 4
    outer_r = size // 2 - 10
    label_r = (inner_r + outer_r) // 2

    _base = alt.Chart(_chart_df)
    _theta = alt.Theta("total:Q", stack=True)
    _donut = _base.mark_arc(innerRadius=inner_r, outerRadius=outer_r).encode(
        theta=_theta,
        color=alt.Color("category:N", scale=alt.Scale(scheme="tableau20"), legend=None),
        tooltip=["category:N", alt.Tooltip("total:Q", format=".0f", title="EUR")],
    )
    _label_enc = {"theta": _theta, "text": alt.Text("category:N")}
    _labels_bg = _base.mark_text(
        radius=label_r,
        fontSize=10,
        fontWeight="bold",
        fill="white",
        stroke="white",
        strokeWidth=4,
    ).encode(**_label_enc)
    _labels_fg = _base.mark_text(
        radius=label_r,
        fontSize=10,
        fontWeight="bold",
        fill="#333333",
    ).encode(**_label_enc)
    return alt.layer(_donut, _labels_bg, _labels_fg).properties(
        width=size,
        height=size,
        title=alt.TitleParams(text=title, subtitle=f"€{total:,.0f}"),
    )


def make_basket_chart(
    df: pl.DataFrame,
    title: str = "",
    width: int = 700,
    height: int = 350,
) -> alt.VConcatChart:
    """Return a two-panel vconcat chart.

    Top panel: stacked bar by basket/month. Bottom panel: category group breakdown.
    df columns: basket_name, year_month, group_name, total_amount.
    Click a bar segment to filter the bottom panel to that basket/month combination.
    """
    sel = alt.selection_point(fields=["basket_name", "year_month"])

    top = (
        alt.Chart(df)
        .transform_aggregate(
            basket_total="sum(total_amount)",
            groupby=["basket_name", "year_month"],
        )
        .mark_bar()
        .encode(
            x=alt.X("year_month:O", title=None),
            y=alt.Y("basket_total:Q", stack=True, title="EUR", axis=alt.Axis(format="~s")),
            color=alt.Color(
                "basket_name:N",
                scale=alt.Scale(scheme="tableau20"),
                legend=alt.Legend(title="Basket"),
            ),
            opacity=alt.condition(sel, alt.value(1.0), alt.value(0.4)),
            tooltip=[
                alt.Tooltip("year_month:O", title="Month"),
                alt.Tooltip("basket_name:N", title="Basket"),
                alt.Tooltip("basket_total:Q", format=".0f", title="EUR"),
            ],
        )
        .add_params(sel)
        .properties(width=width, height=height, title=title)
    )

    bottom = (
        alt.Chart(df)
        .transform_filter(sel)
        .transform_aggregate(
            group_total="sum(total_amount)",
            groupby=["group_name"],
        )
        .mark_bar()
        .encode(
            x=alt.X("group_total:Q", title="EUR", axis=alt.Axis(format="~s")),
            y=alt.Y("group_name:N", sort="-x", title=None),
            color=alt.Color(
                "group_name:N",
                scale=alt.Scale(scheme="tableau10"),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("group_name:N", title="Category group"),
                alt.Tooltip("group_total:Q", format=".0f", title="EUR"),
            ],
        )
        .properties(width=width, title="Category breakdown")
    )

    return alt.vconcat(top, bottom).resolve_scale(color="independent")
