"""Reusable chart builder for the analytics dashboard."""

import altair as alt
import polars as pl


def make_chart_pair(
    expense_monthly_df: pl.DataFrame,
    income_monthly_df: pl.DataFrame,
    year_df: pl.DataFrame,
    total_income: float,
    category_order: list[str],
    period_title: str | None = None,
) -> alt.HConcatChart:
    """Return hconcat(stacked_area + saved_line, year_bars + saved_text)."""
    chart_width, chart_height, year_width = 700, 400, 160

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
            x=alt.value(year_width / 2),
            y=alt.value(15),
            text="t:N",
            color=alt.value(savings_color),
        )
        .properties(width=year_width, height=30)
    )

    title_kwargs: dict[str, str] = {"title": period_title} if period_title else {}
    return alt.hconcat(
        alt.layer(
            alt.layer(areas, labels_bg, labels_fg),
            alt.layer(savings_line, savings_label),
        )
        .resolve_scale(y="independent")
        .properties(width=chart_width, height=chart_height, **title_kwargs)
        .interactive(),
        alt.vconcat(
            alt.layer(year_bars, year_text_bg, year_text_fg).properties(
                width=year_width,
                height=chart_height,
                title="Year total",
            ),
            saved_text,
            spacing=4,
        ),
        spacing=10,
    )
