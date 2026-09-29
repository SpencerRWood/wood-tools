# Migrate legacy OpenProject Versions to R# planning releases

This is a procedure for a separately approved live migration. Do not run it as
part of implementing the planning-release tooling. Keep repository Git tags,
package versions, changelogs, and GitHub Releases unchanged.

1. Export each OpenProject project's Versions, status, IDs, and all work packages
   under each delivery root. Capture Project, Version, Epic, Story, work package
   ID, status, predecessor relations, repository traceability, and any actual
   `Released In` value. Save the pre-migration export and the mapping plan.
2. Classify Versions as historical closed or active. Preserve historical closed
   Versions as history unless a separate archival decision explicitly includes
   them. Do not infer an artifact version from a Version name.
3. Assign each active legacy Version a unique R# name in delivery order, for
   example `R9` and `R10 — Deployment`. Review numeric order, descriptions,
   duplicate names, and any Stories without a Version. Record an explicit
   old-Version-ID to new-R# mapping for each project.
4. Prepare a 21-column implementation workbook per delivery root. Preserve the
   Project → Version → Epic → Story hierarchy, Story IDs, OpenProject IDs,
   parent IDs, and predecessor relationships. Fill `Primary Repository` and
   `Affected Repositories` from verified repository evidence. Keep `Released In`
   blank for unshipped Stories. Preserve already known actual semantic-release
   values separately and restore them through the post-shipment path after the
   planning import; never populate them from R# names.
5. Preview every workbook with `wood project import-workbook ... --json`.
   Review the complete creation/update/reuse set, parent and Version links,
   predecessor relations, repository fields, and unexpected changes. Resolve
   ambiguity and stale IDs before requesting approval to apply. Apply only the
   reviewed plan under the approved workflow.
6. Resume safely by retaining workbook OpenProject IDs and verified root metadata.
   Re-run the plan after interruption: existing Version names, Story IDs, and
   relations should be reused. Never create a second R# mapping for the same
   legacy Version. Preview again if OpenProject changed after the first plan.
7. Read each migrated root through `wood story list` and `wood story get`, then
   compare counts, IDs, hierarchy, Version links, predecessor edges, repository
   traceability, statuses, and unshipped blank `Released In` values with the
   inventory. Record shipped artifact versions from the verified release evidence
   in delivery records; do not infer them from R# names.
8. Remove legacy compatibility only after all active projects have passed the
   comparison, owners have accepted the migrated workbooks, and no active
   automation reads the old names or 18-column layout. Keep historical exports
   and semantic-release history available for audit.
