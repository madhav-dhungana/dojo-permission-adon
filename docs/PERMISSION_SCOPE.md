# Permission model

## Purpose

Add one restricted Writer-style group layer without changing DefectDojo's
built-in Writer role globally.

## Eligibility

The layer may be assigned to a member who is in the default group or is not
currently assigned to a group.

Reader, Writer, and Maintainer membership inside the shared restricted group
activates the layer, including members with native Maintainer access. Global
Reader/Writer/Maintainer or other-group grants do not widen its product scope.
Superusers, global Owners, and Owners of the shared group are exempt. A direct
product Owner retains native permissions on that assigned product.
Do not overwrite, downgrade, or otherwise change roles the member holds in other
groups.

## Group configuration

Installation provisions one shared **Dev Team group**. It adds restrictions to
the member's existing native access and grants no product access itself.
Administrators add existing accounts through normal group administration and
assign the appropriate native role to each individual user on each intended
product. Products must not be assigned to the shared group. No group checkbox
or custom native global role is required.

## Finding and engagement access

A member of the restricted group may:

- View the product, engagement, and finding context required for their work.
- Edit existing findings when their native product role permits it.
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

Use **User Menu → Add or Invite your Team Member**
(`/dojo-permission/accounts/create/`). The new account joins the same shared
group and inherits **all directly assigned products**, with the inviter's exact
native product roles and group membership role. There is no product selector;
Reader remains Reader, and Writer/Maintainer roles are not rewritten. No global
role or configuration permissions are granted. Owner or unknown product roles
require the normal administrator workflow instead. An administrator must assign
existing accounts; restricted members cannot browse or select arbitrary existing accounts.
Existing roles and memberships remain unchanged. Reader, Writer, and Maintainer
membership remains subject to the policy; Owner membership is exempt.

User lists and selectors for restricted members are limited to participants
in their directly assigned products rather than the global user directory.

Product assignments belong to individual users: the shared group is a deny-only
marker, not a product access grant. Nested invitations inherit the next inviter's
complete assigned set in the same way. Unassigned products and their underlying
engagements and findings remain inaccessible.
Cross-product metrics, product-type overview pages, and global finding templates
are unavailable to shared restricted members because they can include data from
unassigned products.

## Authorization boundary

Authorization is scoped through the relevant DefectDojo product/engagement and
its findings. The implementation must use the DefectDojo 2.58.3 RBAC model and
must not depend on the `authorized_users` model introduced in 2.59.0.
