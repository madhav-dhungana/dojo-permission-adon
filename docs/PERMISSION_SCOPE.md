# Confirmed Permission Scope

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

The normal DefectDojo group create/edit form includes the checkbox **Do you want
to apply delete/edit restrictions to this group?**. Checking it activates the
library-owned restricted policy for that group. Unchecking it deactivates the
additional policy without renaming the group or changing any group membership,
group role, or global role.

## Finding and engagement access

A member of the restricted group may:

- View the product, engagement, and finding context required for their work.
- Edit existing findings.
- Use existing finding update actions, including close, reopen, mitigate,
  accept risk, and bulk edit.

A member of the restricted group may not:

- Create engagements.
- Create tests.
- Import or re-import scan results.
- Create findings.
- Copy findings.
- Delete findings.
- Delete engagements.

The existing update behavior stays intact. Actions that create new finding
records, directly or through a scan import, re-import, or copy, are outside the
restricted role.

## User management

A member may:

- Create or invite a new DefectDojo account by email.
- Invite a user who will authenticate through SSO.
- Add an existing DefectDojo account to the relevant restricted group.

The added user inherits only the relevant restricted group and the requester's
role in that group, not every group to which the requester belongs. Existing
roles and memberships in other groups remain unchanged. Reader, Writer, and
Maintainer membership in the restricted group remains subject to the policy;
Owner membership is exempt.

## Authorization boundary

Authorization is scoped through the relevant DefectDojo product/engagement and
its findings. The implementation must use the DefectDojo 2.58.3 RBAC model and
must not depend on the `authorized_users` model introduced in 2.59.0.
