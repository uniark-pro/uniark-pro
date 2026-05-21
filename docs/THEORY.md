# Axiomatic Construction of a Trading System

> An attempt to formalize a price-action trading framework as a layered
> deductive system, in the spirit of Euclidean geometry: each layer
> introduces only the minimum new vocabulary, and all higher-layer
> concepts are constructed from lower-layer primitives.
>
> **This document is a theoretical specification.** It is not a trading
> recommendation, not a profit claim, and not a backtested strategy.
> See the project [README](../README.md) for the project's intent.

---

## I. Axiom Layer

**Axiom.** No state of the market is permanent. At any given timescale,
state changes occur repeatedly.

**Theorem.** At any given timescale, the market contains at least one
three-segment continuous-trend structure
S1 → S2 → S3
(up-down-up, or down-up-down).

---

## II. Structural Layer

**Definition (Turning Point).** A price point that separates two
continuous adjacent segments of opposite direction.

**Definition (Segment).** A price path from one turning point to the
next.

**Definition (Three-Segment Structure).** A structure formed by three
consecutive segments S1 → S2 → S3, in
which adjacent segments are of opposite direction.

**Definition (Overlap Range).** Within a three-segment structure
{S1, S2, S3}:

- The upper bound of the overlap range is the **lowest** of the three
  segments' high points.
- The lower bound is the **highest** of the three segments' low points.

The price interval between this lower and upper bound is the overlap
range.

---

## III. State Layer

### Base State: Consolidation (盘整)

A consolidation is established when:

1. The structure contains at least three segments, and
2. The first three segments admit a non-empty overlap range.

The **consolidation range** is the overlap range of the first three
segments of the consolidation structure.

> **Anchor invariance.** Once the first three segments have established
> a consolidation range, subsequent extensions of the structure do
> *not* alter that range. The consolidation range is a fixed anchor.

### Relations Between Consolidations

- **Lifted consolidation** (盘整抬高): given two consecutive
  consolidations, the **low** of the later consolidation range is
  strictly above the **high** of the earlier one.
- **Sunken consolidation** (盘整下降): the **high** of the later
  consolidation range is strictly below the **low** of the earlier one.

### Compound States: Uptrend / Downtrend

- **Uptrend** (上涨): contains at least two consolidation structures,
  and they are lifted relative to each other.
- **Downtrend** (下跌): contains at least two consolidation structures,
  and they are sunken relative to each other.

**Key constraint.** Two consecutive consolidation *structures* may
overlap in their underlying segments, but their **consolidation
ranges** must be disjoint.

---

## IV. Transition Layer

### Definition: Consolidation Breakdown

The current consolidation is **broken** if and only if all three of
the following hold:

1. **Trigger.** A segment leaves the existing consolidation range.
2. **Completion.** That segment participates in the construction of a
   new three-segment structure.
3. **Confirmation.** The new consolidation range is disjoint from the
   old one.

**Direction of transition:**

- New range's low > old range's high → lifted → transition to
  **uptrend**.
- New range's high < old range's low → sunken → transition to
  **downtrend**.

### Definition: Uptrend Failure

In an uptrend, an upward segment breaks above the most recent
consolidation range, but during the construction of the next
three-segment structure the price re-enters and overlaps with the
old consolidation range. (Construction fails.)

**Effect:** Uptrend failure is established. The transition is to either
**uptrend → consolidation** or **uptrend → downtrend**.

### Definition: Downtrend Failure

Symmetric to uptrend failure: a downward segment breaks below the most
recent consolidation range but the new three-segment construction
re-overlaps with the old range.

**Effect:** Downtrend failure is established. The transition is to
either **downtrend → consolidation** or **downtrend → uptrend**.

---

## V. Predictive Layer

### Definition: Force (力度)

The **force** of a segment Si is the sum of the absolute
values of the MACD histogram bar areas over the time range of
Si:

> Force(Si) = Σ |hist(t)|, where t ∈ Si

### Definition: Co-directional Segments (同向段)

In a three-segment structure
S1 → S2 → S3, if S1 and
S3 share the same direction (and S2 is the
opposite-direction connector), then S1 and S3
are co-directional segments.

### Definition: Force Decay (力度衰竭)

Comparing the forces of co-directional segments S3 and
S1, force decay is established when:

> Force(S3) < Force(S1) × 50%

> The 50% threshold is an empirical parameter and may be tuned for
> different markets and timeframes.

### Definition: Divergence

Comparing co-directional segments S1 and S3:
divergence holds if force decay is established **and** S3
makes a new price extreme relative to S1 (a higher high in
an uptrend; a lower low in a downtrend).

- **Trend Divergence (Strong, 趋势背离).** S1 and
  S3 are connected by a *consolidation structure* S2,
  and divergence holds.
- **Three-Segment Divergence (Weak, 三段背离).** S1 and
  S3 are connected by a *single opposite segment* S2,
  and divergence holds.

---

## VI. Execution Layer

> **Reminder.** The signals below are the theoretical loci of
> structural change; they are not trading instructions, and have not
> been validated by backtesting in this project.

### Breakdown Sell (破坏卖出)

Conditions:

- Currently in **uptrend** state.
- **Trend divergence** holds, and the uptrend is broken.
- S3 breaks upward through the consolidation range of
  S2.
- Force(S3) < Force(S1) × 50%.

→ **Sell zone:** near the new high price.

### Breakdown Buy (破坏买入)

Symmetric to Breakdown Sell:

- Currently in **downtrend** state.
- Trend divergence holds, and the downtrend is broken.
- S3 breaks downward through the consolidation range of
  S2.
- Force(S3) < Force(S1) × 50%.

→ **Buy zone:** near the new low price.

### Consolidation Sell (盘整卖出)

Conditions:

- S1 is a downward segment; the MACD fast and slow lines
  are operating below the zero axis (or have effectively crossed
  below zero).
- A consolidation structure S2 is forming (with its own
  three internal segments); during S2 the MACD fast and
  slow lines pull back toward the zero axis.
- S2 is completed; the MACD fast and slow lines fail to
  break above zero.
- Force(S3) ≥ Force(S1) × 50%.

→ **Sell zone:** near the start of the new downward segment
S3.

### Consolidation Buy (盘整买入)

Symmetric to Consolidation Sell:

- S1 is an upward segment; MACD fast and slow lines run
  above the zero axis (or have effectively crossed above zero).
- A consolidation structure S2 is forming; MACD fast and
  slow lines retrace down to the zero axis.
- S2 completes; the MACD fast and slow lines lift up from
  the zero axis again.
- Force(S3) ≥ Force(S1) × 50%.

→ **Buy zone:** near the start of the new upward segment S3.

---

## Notes on Scope and Limitations

This framework is a **descriptive language** for price action, not a
predictive guarantee. Its claims are conditional:

- It does not assert that any market always exhibits clean
  three-segment structures; it asserts only the existence of *at
  least one* such structure at any given timescale.
- It does not claim that divergence implies a price reversal will
  occur; it only labels the structural conditions under which
  reversal candidates can be located.
- The 50% force-decay threshold is empirical, not derived; users may
  reasonably tune this parameter for different instruments and
  timeframes.
- Specific market regimes (extreme one-way trends, illiquid markets,
  policy-driven halts, structural breaks) may invalidate the
  framework's foundational assumptions, including the existence
  axiom.

The contribution of this work is to make the framework
**falsifiable**: every claim is precise enough to be tested,
contradicted, or refined. That is the necessary precondition for any
trading system to be a rational tool rather than a belief.

---

*Original Chinese version: see [`THEORY_ZH.md`](THEORY_ZH.md).*
