# Metadata standardization

## BIDS `participants.tsv` standardization

The `npdb standarize bids` will :

- Standardize the header fields of provided `participants.tsv` file(s), following a given standard (the NeuroBagel standard by default).
- Add missing fields with empty values, following the same standard.
- Generate or update a `participants.json` file with associated standardized metadata fields descriptions.

### Common usage

```bash
npdb standardize bids <bids_root_directory> \
   # (Optional) if not given, the script outputs in the input directory structure
   --output <output_directory> \
   # (Optional) standardization mode, default to 'manual'
   --mode <manual|assist|auto|full-auto>
```

> [!IMPORTANT]
> The assisted and automated modes (`assist`, `auto` and `full-auto`) require additional dependencies to be installed. Run :
>
> ```bash
> uv sync --active --quiet --extra annotation-automation
> uv run playwright install --with-deps chromium
> ```

> [!WARNING]
> The automated modes (`auto` and `full-auto`) use **state-of-the-art language models** to replace human intervention in all parts of the standardization process. **There is no guarantee that the generated output will be correct. Always check the generated output for potential errors**.

## Custom header mapping

The `npdb standardize bids` command also accepts a custom header mapping file (`--header-map`), in JSON format, to specify the desired output headers and the input variants to consider for each of them. For example, the following mapping :

```json
{
  "age": ["age", "age_years", "years_old"],
  "sex": ["sex", "gender"]
}
```

will standardize any of the input variants (`age`, `age_years` or `years_old`) to the output header `age`, and any of the input variants (`sex` or `gender`) to the output header `sex`.
