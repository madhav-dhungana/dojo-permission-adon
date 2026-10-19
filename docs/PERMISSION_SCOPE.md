# Permission model

## Purpose

Add one restricted Writer-style group layer without changing DefectDojo's
built-in Writer role globally.

## Eligibility

The layer may be assigned to a member who is in the default group or is not
currently assigned to a group.

Do not apply the restricted layer to a member who has independent Owner or
Maintainer access. A global Writer assignment does not bypass the restricted
layer. Reader, Writer, and Maintainer membership inside the restricted group
activates the layer. Owner membership inside that group is exempt, as are
superusers.
Do not overwrite, downgrade, or otherwise change roles the member holds in other
groups.

## Group configuration

Installation provisions **Dev Team group** and a managed restricted group named
**Dev Team group - Product `<id>`** for each product. The default group adds
restrictions to the member's existing native access; it grants no product
access. Each product group is bound to exactly one product with Writer access.
Administrators assign existing accounts through DefectDojo's normal group
administration. No group checkbox is required, and no custom native global role
is created.

## Finding and engagement access

A member of the restricted group may:

- View the product, engagement, and finding context required for their work.
- Edit existing findings.
- Use existing finding update actions, including close, reopen, mitigate,
  accept risk, and bulk edit.

A member of the restricted group may not:

- Create engagements.
- Edit or copy engagements.
- Create tests.
- Import or re-import scan results.
- Create findings.
- Copy findings.
- Delete findings.
- Delete engagements.
- Delete products or change product/group access assignments.

The existing update behavior stays intact. Actions that create new finding
records, directly or through a scan import, re-import, or copy, are outside the
restricted role.

## User management

A member may:

- Add or invite a new DefectDojo team member by email.
- Invite a user who will authenticate through SSO.

The requester selects one product where they can already edit findings, so
read-only product access cannot be delegated as Writer access. The new account joins
only that product's managed restricted group and receives no global role or
configuration permissions. An administrator must assign existing accounts;
restricted members cannot browse or select arbitrary existing accounts.
Existing roles and memberships remain unchanged. Reader, Writer, and Maintainer
membership remains subject to the policy; Owner membership is exempt.

User lists and selectors for restricted members are limited to participants
in their visible products rather than the global user directory.

## Authorization boundary

Authorization is scoped through the relevant DefectDojo product/engagement and
its findings. The implementation must use the DefectDojo 2.58.3 RBAC model and
must not depend on the `authorized_users` model introduced in 2.59.0.
