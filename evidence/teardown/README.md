# Teardown baseline

The reference deployment's inventory is omitted from this public export because
it identifies the operator and real cloud resources. No example baseline is
installed in its place: teardown must compare against your own project.

After bootstrap and before creating the estate, capture your own
`evidence/teardown/baseline.json` following
[the first-deploy guide](../../docs/first-deploy.md#7-capture-the-teardown-baseline).
Keep the unsanitised inventory private; it can contain personal email addresses,
IAM grants, project numbers and resource identifiers.
