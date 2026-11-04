# house_price_estimate

Estimates the current market value of residential addresses in England from
bulk open data, searched entirely on the local machine.

## Privacy

**Addresses never leave the machine.**

- `download` is the only command that uses the network. It takes no address
  input. It fetches whole datasets from fixed URLs in
  [config.py](house_price_estimate/config.py).
- `estimate`, `backtest` and `calibrate` run inside `offline.no_network()`. This makes any
  Python socket connection or DNS lookup raise `NetworkForbiddenError`.
- DuckDB is opened read-only with extension auto-install/auto-load and
  external access disabled, and its configuration is then locked, so it
  cannot fetch anything either.
- Addresses are never logged. Output CSVs contain only your id column and
  the results, unless you pass `--keep-address`.
- By default, `estimate` output also leaves out fields that could identify the
  property, and gives the price as a band:
  - The method is deterministic and uses only public data. Anyone who knows
    the area could value every address in it and match precise outputs
    back to one address.
  - The fields left out are `postcode`, `property_type`, `floor_area`,
    `n_sales`, `last_sale_*`, `repeat_sale_est`, `repeat_weight` and
    `comps_*`, plus `ppd_match_score`, `hpi_series` and the exact `estimate`.
  - The estimate is replaced by its `price_band`. Bands are £25k wide up to
    £500k, £50k to £1m, £250k to £2m and £1m to £5m, with one open band
    above that.
  - `low`/`high` are widened outwards to band edges.
  - In a Norfolk & Suffolk backtest, the share of sampled sales whose
    output row was unique fell from 100% (with `comps_est`) or 36% (exact
    estimate only) to 3%. 80% of rows were shared by at least 10 sampled
    sales, and the sample covered under 1% of homes.
  - The banded range contained the sale price 88% of the time instead of
    81%. See section 12 of the notebook.
  - Pass `--keep-identifiable` to get everything, including the exact
    estimate and range. Treat that output as identifiable.
- `data/` and `*.csv` are gitignored. Input and output files may still be
  personal data, so store them as such.

For extra assurance, run estimation in a container without networking
(`docker run --network none ...`) or with `unshare -rn`.

## Data

| Dataset | Used for | Size |
|---|---|---|
| [HM Land Registry Price Paid Data](https://www.gov.uk/government/statistical-data-sets/price-paid-data-downloads) (complete file) | sale history of the property and of nearby comparables | 5.5 GB |
| [UK House Price Index](https://www.gov.uk/government/collections/uk-house-price-index-reports) full file | indexing past prices to the latest month, by local authority and property type | 35 MB |
| [ONS Postcode Directory](https://geoportal.statistics.gov.uk/) | postcode → grid reference, local authority, region | 250 MB zip |
| [EPC domestic certificates](https://get-energy-performance-data.communities.gov.uk/) (optional) | floor area and property type | several GB |

All of these are under the Open Government Licence. The EPC bulk files need
a free GOV.UK One Login account, so you download them by hand: put the zip
or CSV(s) in `data/raw/epc/`. Without EPC data the pipeline still works, but
it can't adjust for floor area.

You need about 60 GB of free disk for the downloads, the build and the
temporary files.

## Usage

```bash
cd py/house_price_estimate
pip install -r requirements.txt

python -m house_price_estimate download          # ~6 GB
# optional: put the EPC zip in data/raw/epc/
python -m house_price_estimate build             # builds data/processed/hpe.duckdb
python -m house_price_estimate calibrate         # fits the estimate ranges (~3 min)

# one address
python -m house_price_estimate estimate --address "10 Downing Street, London SW1A 2AA"

# a CSV with columns id,address (or a separate postcode column)
python -m house_price_estimate estimate --input addresses.csv --id-col id \
    --address-col address [--postcode-col postcode] --out estimates.csv
# add --keep-identifiable for the exact estimate, postcode, sale history etc.

# accuracy on 2025 sales, each valued using only earlier data
python -m house_price_estimate backtest --year 2025 --n 2000
```

Use `--data-dir` (or the `HPE_DATA_DIR` environment variable) to keep the
data somewhere else. To refresh the data, re-run `download`, `build` and
`calibrate`: Land Registry updates monthly, and the HPI lags by about two
months. If you use `--no-epc`, also run `calibrate --no-epc`, because each
configuration has its own calibration file.

From Python:

```python
from house_price_estimate.estimate import Estimator
from house_price_estimate.offline import no_network

with no_network():
    est = Estimator("data/processed/hpe.duckdb")
    v = est.estimate_address("Flat 2, 10 High Street, Norwich NR2 1AA")
    print(v.estimate, v.low, v.high, v.method, v.confidence)
```

## Method

1. **Parse.** Extract and normalise the postcode, then the house/flat
   identifiers (numbers, `12A`, `Flat B`) and the house name.
2. **Match.** Compare against every Land Registry sale and EPC certificate in
   the same postcode. Numbered addresses need the same identifiers in the
   same order (`exact`) or in a different order (`fuzzy`), with
   street-name similarity breaking ties. Named houses are fuzzy-matched on
   the name. A flat never matches the whole building.
3. **Attributes.** Property type comes from the property's own sales, else
   from its EPC, else from the most common type in the postcode. Floor area
   comes from the EPC.
4. **Repeat sale.** Index the last sale price with the UK HPI from the sale
   month to the latest month. The series used is local authority and
   property type, falling back to region and then England.
5. **Comparables.** Take sales of the same type from the last 5 years. Search
   within 500 m, widening to 1, 2 and 5 km until there are at least 10.
   New builds are excluded when possible. Each sale is indexed to the same
   month and weighted by distance and recency. When floor areas are known,
   each comparable is scaled by `(target area / comp area) ^ 0.75`. The
   estimate is the weighted median.
6. **Blend.** The repeat-sale weight is `0.9 × exp(−years since sale / 40)`,
   halved if that sale was a new build. This was fitted on a backtest: the
   indexed previous sale beats comparables until it is about 20 years old.
7. **Range and confidence.** `calibrate` backtests 10,000 sales from two
   years ago. For each `calibration_group`, it records the 10th and 90th
   percentiles of actual price ÷ estimate. The groups are:
   - repeat sale ≤5, 5–15 or >15 years old
   - comparables with or without floor area
   - repeat sale only

   `low`/`high` are the estimate × those percentiles, an empirical 80%
   interval. `confidence` depends on the interval's width: ≤40% of the
   estimate is `high`, ≤50% is `medium`, otherwise `low`. A group with
   too few sales to calibrate gets the widest range and `low`. Without a
   calibration file, the range falls back to the comparables' interquartile
   range, and confidence to a simple rule.

Model parameters are in `ModelParams` in
[config.py](house_price_estimate/config.py).

### Output columns

| column | meaning |
|---|---|
| `status` | `ok`, `no_postcode`, `postcode_not_found` |
| `ppd_match`, `epc_match` | `exact`, `fuzzy` or `none` |
| `property_type`\* | D, S, T, F (detached, semi, terraced, flat) |
| `property_type_source` | where the type came from: `ppd`, `epc` or `postcode_mode` |
| `postcode`\*, `floor_area`\*, `hpi_series`\* | normalised postcode, EPC floor area (m²), HPI series used |
| `n_sales`\*, `last_sale_*`\* | the property's own sale history |
| `repeat_sale_est`\*, `repeat_weight`\* | the last sale indexed to the valuation month, and its weight in the blend |
| `comps_n`\*, `comps_radius_m`\*, `comps_est`\*, `comps_floor_area_adjusted`\* | the comparables estimate |
| `ppd_match_score`\* | address similarity of the matched sale (0–100) |
| `price_band`, `price_band_low`, `price_band_high` | the estimate, rounded into a band, e.g. `£250k–£275k`. This is **not** a range for the sale price: only about 3 in 10 homes sell within their estimate's band. Use `low`–`high` for that. |
| `price_band_mid` | the band's midpoint, the suggested single value for analysis (empty for the open `£5m+` band) |
| `estimate`\* | blended estimate, to the nearest £1,000 |
| `low`, `high` | the likely range of the sale price, designed to contain it 80% of the time. By default it is widened to band edges, and contains it about 88% of the time. With `--keep-identifiable` it is exact, to the nearest £1,000. |
| `method`, `calibration_group`, `confidence` | how the estimate was made, which error distribution sets its range, and `high`/`medium`/`low` |
| `valuation_month` | the HPI month that the values refer to |

\* Only with `--keep-identifiable`.

### Accuracy

These figures are from the 2025 backtest (3,000 sales, each valued from
earlier data only), with calibration on 2024 sales. Median error is the
median absolute percentage error. Coverage is the share of actual prices
that fell inside `low`–`high`.

| | median error | within ±10% | within ±20% | coverage |
|---|---|---|---|---|
| all, with EPC | 9.2% | 53% | 78% | 81% |
| confidence high (repeat sale ≤15 years) | 7.8% | 60% | 85% | 81% |
| confidence medium | 10.6% | 47% | 73% | 81% |
| no earlier sale, with EPC floor area | 11.8% | 44% | 69% | 80% |
| all, without EPC | 11.1% | 46% | 73% | 80% |

[notebooks/performance.ipynb](notebooks/performance.ipynb) has a fuller
breakdown on 20,000 sales from August 2025 to July 2026. It splits the
results by evidence, confidence, property type, region, value band,
rural/urban class, match quality and age of the last sale. It also has a
separate section on Norfolk and Suffolk, a section on the effect of price
bands with advice on using them, and a summary of where the estimates are
weak. To re-run it after a rebuild, install the notebook
extras in `requirements.txt`, set `RECOMPUTE = True`, and run
`jupyter nbconvert --to notebook --execute --inplace notebooks/performance.ipynb`.
This takes about 20 minutes.

The address matcher finds an earlier sale for 72% of sales. Only 71% have
an earlier sale under exactly the same address, so the rest are properties
that haven't sold since 1995.

### Limitations

- Land Registry covers only market sales registered in England and Wales.
  Right-to-buy, most shared ownership, and properties that have never sold
  since 1995 have no sale history of their own.
- Condition, extensions since the last sale, gardens, views and parking are
  not modelled. Roughly one estimate in five falls outside its range.
- The UK HPI is revised after publication, so backtests have a small
  look-ahead.

## Tests

```bash
python -m pytest tests
```

The tests build a synthetic mini dataset in the real file formats. They
cover parsing, matching, indexing, blending, the CLI and the network guards.

## Method references

These are the established methods that each step corresponds to. The
specific parameters, such as search radii, weights, the 0.75 exponent, the
blend weight and the band widths, are this pipeline's own choices, tuned or
checked on backtests.

- **Repeat-sale estimate.** Step 4 uprates the property's own last sale
  price with a house price index, the repeat-sales idea these papers
  introduced for building indexes.
  - Bailey, M. J., Muth, R. F. & Nourse, H. O. (1963). A regression method
    for real estate price index construction. *Journal of the American
    Statistical Association*, 58, 933–942.
    [doi:10.1080/01621459.1963.10480679](https://doi.org/10.1080/01621459.1963.10480679)
  - Case, K. E. & Shiller, R. J. (1987). Prices of single family homes since
    1970: new indexes for four cities. NBER Working Paper 2393.
    [doi:10.3386/w2393](https://doi.org/10.3386/w2393)
- **Comparables.** Step 5 values the property from nearby recent sales of
  the same type, weighted by distance and recency, which is the
  sales-comparison approach reviewed in this paper.
  - Pagourtzi, E., Assimakopoulos, V., Hatzichristos, T. & French, N.
    (2003). Real estate appraisal: a review of valuation methods. *Journal
    of Property Investment & Finance*, 21, 383–401.
    [doi:10.1108/14635780310483656](https://doi.org/10.1108/14635780310483656)
- **Floor-area adjustment.** Step 5 scales each comparable's price by
  relative floor area, a single-attribute form of the hedonic pricing model,
  in which price reflects a property's characteristics.
  - Rosen, S. (1974). Hedonic prices and implicit markets: product
    differentiation in pure competition. *Journal of Political Economy*,
    82, 34–55. [doi:10.1086/260169](https://doi.org/10.1086/260169)
- **Range calibration.** Step 7 sets `low`/`high` from the 10th and 90th
  percentiles of actual ÷ estimate on held-out sales in each evidence group,
  a per-group ("Mondrian") form of split conformal prediction.
  - Lei, J., G'Sell, M., Rinaldo, A., Tibshirani, R. J. & Wasserman, L.
    (2018). Distribution-free predictive inference for regression. *Journal
    of the American Statistical Association*, 113, 1094–1111.
    [doi:10.1080/01621459.2017.1307116](https://doi.org/10.1080/01621459.2017.1307116)
  - Vovk, V., Gammerman, A. & Shafer, G. (2005). *Algorithmic Learning in a
    Random World*. Springer. [doi:10.1007/b106715](https://doi.org/10.1007/b106715)
- **Land Registry–EPC link.** The build step attaches EPC floor areas to
  Land Registry sales by matching addresses within a postcode, a simpler
  version of the linkage this paper made between the same two datasets.
  - Chi, B., Dennett, A., Oléron-Evans, T. & Morphet, R. (2021). A new
    attribute-linked residential property price dataset for England and
    Wales, 2011–2019. *UCL Open: Environment*.
    [doi:10.14324/111.444/ucloe.000019](https://doi.org/10.14324/111.444/ucloe.000019)
- **Banding.** The default `estimate` output replaces exact values with
  price bands and hides quasi-identifying fields, applying the generalisation
  and suppression these papers describe to resist re-identification by
  linkage.
  - Sweeney, L. (2002). k-anonymity: a model for protecting privacy.
    *International Journal of Uncertainty, Fuzziness and Knowledge-Based
    Systems*, 10, 557–570. [doi:10.1142/S0218488502001648](https://doi.org/10.1142/S0218488502001648)
  - Samarati, P. (2001). Protecting respondents' identities in microdata
    release. *IEEE Transactions on Knowledge and Data Engineering*, 13,
    1010–1027. [doi:10.1109/69.971193](https://doi.org/10.1109/69.971193)

## Data references

All datasets are published under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Check each publisher's page for the exact attribution wording and any
third-party (Ordnance Survey, Royal Mail) terms before publishing derived
results.

- **HM Land Registry Price Paid Data**, complete file. This is every
  residential sale in England and Wales since 1995, used for sale histories
  and comparables. Contains HM Land Registry data © Crown copyright and
  database right.
  https://www.gov.uk/government/statistical-data-sets/price-paid-data-downloads
- **UK House Price Index**, full file. This is published by HM Land Registry
  with the Office for National Statistics, and used to uprate past prices by
  local authority and property type.
  https://www.gov.uk/government/collections/uk-house-price-index-reports
- **ONS Postcode Directory (ONSPD)**. This is published by the Office for
  National Statistics, and used for postcode grid references, local
  authority, region and county. It also supplies the 2021 Rural Urban
  Classification used in the performance notebook. Its licence also requires
  Ordnance Survey and Royal Mail copyright statements; see the ONSPD user
  guide. https://geoportal.statistics.gov.uk/
- **Energy Performance Certificates, domestic bulk data**. This is published
  by the Ministry of Housing, Communities and Local Government, and used for
  floor area and property type. The address fields may carry extra Royal
  Mail and Ordnance Survey terms.
  https://get-energy-performance-data.communities.gov.uk/
