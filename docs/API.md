# API guide

How a client talks to this backend. The exhaustive schema is
[schema.graphql](schema.graphql) (generated, always current); this guide explains
the conventions and lists the operations by module. Every operation's description
in the schema names the permission it needs (`permissions=['…']`).

## 1. Endpoints

| Path | What |
|------|------|
| `POST /graphql` | The API. GraphiQL at `GET /graphql` in development |
| `POST /oauth2/access_token` | Sign in (`grant_type=password`) and refresh (`grant_type=refresh_token`); form-encoded |
| `POST /oauth2/revoke_token` | Sign out one token |
| `GET  /oauth2/providers` | The external sign-in providers the sign-in page should offer |
| `POST /oauth2/external/<provider>` | Sign in with an OpenID Connect ID token (`id_token=`), or complete 2FA (`mfa_ticket=&otp=`) |
| `POST /files/upload/` | Upload (multipart `file` or JSON `base64_string`) |
| `POST /files/download/` | A file as base64 (`uid` or `file_path`) |
| `GET  /files/<uid>/` | Stream a file (public files need no token) · `DELETE` removes it |
| `POST /files/public-image/` | A public file as base64, no token |
| `GET  /healthz`, `GET /readyz` | Liveness, readiness |
| `/oauth2/authorize…`, `/oauth2/token`, `/oauth2/userinfo`, `/.well-known/openid-configuration`, `/.well-known/jwks.json` | The SSO provider (optional module) |

Everything is also served under `/<API_MOUNT_PREFIX>/` (default `backend`) for
gateways that route by prefix.

Send `Authorization: Bearer <access_token>`, `Accept-Language: en` or `sw`, and
optionally `X-Request-ID` (echoed back; minted when absent).

## 2. Signing in

```
POST /oauth2/access_token
Content-Type: application/x-www-form-urlencoded

grant_type=password&username=asha@example.org&password=…&client_id=<OAUTH_CLIENT_ID>[&otp=123456]
```

Success (`200`):

```json
{
  "success": true, "code": 8000, "message": "Login successful",
  "access_token": "…", "refresh_token": "…", "token_type": "Bearer", "expires_in": 3600,
  "user": { "uid": "…", "email": "…", "first_name": "…", "authorities": ["can_view_users"], "roles": [...], "mfa_enabled": false, ... },
  "requirements": { "mfa_enrolment_required": false, "password_change_required": false, "password_expires_at": null }
}
```

Refusals (`400`) carry `code` + `message`: `8002` wrong credentials, `8003` locked,
`8010–8013` account gates, `8014` a two-factor code is needed (resend with `otp=`),
`9018` throttled. `requirements` tells the SPA to route to the change-password or
2FA-setup screen first: until it complies, the API answers `8015` / `8016` to every
other operation.

Refresh: `grant_type=refresh_token&refresh_token=…&client_id=…`. Sign out:
`POST /oauth2/revoke_token` with `token=…&client_id=…`, or the `revokeMySession` /
`revokeAllMySessions` mutations.

External sign-in: obtain an ID token from the provider in the SPA, then
`POST /oauth2/external/google` with `id_token=…` → the same envelope as above
(or `8014` + `mfa_ticket` when 2FA is on; complete with `mfa_ticket=&otp=`).

## 3. The envelope

```json
{ "status": true, "code": 9001, "message": "Operation successful", "data": {…}, "dataList": [] }
```

`status` is the boolean the code maps to; `data` holds one object, `dataList` a
list. Paginated operations (`…Pageable`) answer:

```json
{ "status": true, "code": 9001, "message": "…",
  "content": [ … ],
  "page": { "number": 1, "size": 20, "totalElements": 57, "totalPages": 3, "hasNext": true, "hasPrevious": false } }
```

Always read `code`, never HTTP status — GraphQL answers `200` for business
outcomes. A GraphQL-level error (malformed document, unknown field, a query over the
limits) answers `400` with the standard `errors` array.

### Pagination and search

```graphql
query ($searchDto: SearchDataDto!, $filtering: UserFilterInput) {
  getAllUsersPageable(searchDto: $searchDto, filtering: $filtering) {
    code content { uid email } page { totalElements hasNext }
  }
}
```

```json
{ "searchDto": { "pageableParam": { "pageNumber": 1, "itemsPerPage": 20, "sortBy": "createdAt", "sortDirection": "DESC" },
                "searchFields": [ { "fieldName": "email", "searchType": "Like", "fieldValue": "asha" } ] },
  "filtering": { "accountType": "Internal" } }
```

`itemsPerPage` is capped by `GRAPHQL_MAX_PAGE_SIZE`. `searchFields` applies
`searchType` (`Equals`, `NotEqual`, `Like`, `Between`, `GreaterThan`, … — see
`SearchOperationTypeEnum` in the schema) to the named fields; `filtering` is the operation's typed filter input.

### Status codes

| Code | Meaning |
|------|---------|
| `9000` | Not authorised (not signed in, or missing permission / account type) |
| `9001` | Success |
| `9002` | Invalid input |
| `9003` | Not found |
| `9004` | Unexpected failure — the message carries a **request id**; give it to support |
| `9005` | Required argument missing (the message names it) |
| `9006` / `9007` | Duplicate / already in use |
| `9015` | In use, cannot be deleted |
| `9018` | Too many attempts — slow down |
| `8000–8018` | Sign-in outcomes and account gates (§2) |
| `9010–9017`, `9120–9126` | Password and two-factor rules |
| `9013`, `9014`, `9020–9024`, `9100–9104` | Registration and account rules |
| `9140–9142` | Roles |
| `9160–9163` | Notifications |
| `9180–9186` | Files |
| `9200–9201` | Runtime settings |
| `9220–9222` | Endpoint-error workflow |
| `9240–9241` | Workflows |
| `8020–8024`, `9025–9027` | SSO provider |
| `10000+` | Business apps (each declares its band) |

Messages are translated per `Accept-Language`; the full list with both languages
is in each app's `status_codes.json`.

## 4. Operations by module

Names are stable; arguments and fields are in the schema. *(A)* = any signed-in
account, *(P)* = needs the named permission, *(public)* = no token.

### Accounts (`users`)
Queries: `getRegistrationStatus` (public) · `getUser`, `getAllUsersPageable`,
`exportUsers` (P) · `getUserConsents` (A) · `exportMyData` (A).
Mutations: `register`, `validateRegistrationOtp`, `resendRegistrationOtp` (public,
throttled) · `updateMyProfile`, `requestEmailChange`, `confirmEmailChange`,
`requestPhoneVerification`, `confirmPhoneVerification`, `acceptConsents`,
`requestAccountDeletion`, `deleteMyAccount` (A) · `createUser` (invitation),
`updateUser`, `activateUser`, `deactivateUser`, `setUserAccountStatus`,
`restoreUser`, `deleteUser`, `assignRolesToUser`, `resendUserInvitation`,
`cancelUserInvitation` (P).

### Sign-in and security (`authentication`)
Queries: `meQuery` (A — who am I, authorities, roles, requirements),
`getSignInProviders` (public), `getMySessions`, `getMyLoginHistoryPageable`,
`getMySecurityEventsPageable` (A) · `getLoginHistoryPageable`,
`getAuthenticationEventsPageable`, `getLdapConfigs` (P).
Mutations: `changeMyPassword`, `requestPasswordReset`, `setPassword` (reset and
invitation links), `beginMfaEnrollment`, `confirmMfaEnrollment`,
`regenerateMfaRecoveryCodes`, `disableMfa`, `revokeMySession`,
`revokeAllMySessions` (A / public where stated) · `sendUserPasswordReset`,
`requireUserPasswordChange`, `resetUserMfa`, `revokeUserSessions`,
`createLdapConfig`, `updateLdapConfig`, `deleteLdapConfig`, `pullLdapUsers` (P).

### Roles and permissions (`rbac`)
`getAllPermissionGroups`, `getAllRolesPageable`, `getRole` · `createRole`,
`updateRole`, `deleteRole`, `assignPermissionsToRole` (P).

### Notifications (`notifications`)
Mine (A): `getMyNotifications`, `getMyInAppNotifications`,
`getMyInAppNotificationsPageable`, `getMyUnreadNotificationCount`,
`getMyNotificationPreferences`, `markInAppNotificationRead`,
`setMyNotificationPreference`.
Administration (P): `getNotificationsPageable`, `resendNotification`,
`getNotificationCategories`, `getNotificationTemplates`,
`createNotificationTemplate`, `updateNotificationTemplate`,
`deleteNotificationTemplate`, `getEmailConfigurations`,
`createEmailConfiguration`, `updateEmailConfiguration`,
`deleteEmailConfiguration`, `testEmailConfiguration`, `getSmsProviders`,
`getSmsConfigurations`, `createSmsConfiguration`, `updateSmsConfiguration`,
`deleteSmsConfiguration`, `testSmsConfiguration`.

### Files (`attachments`)
`getMyAttachmentsPageable`, `getEntityAttachments`,
`getActiveAttachmentDefinitions`, `deleteAttachment` (A, policy-checked) ·
`getAttachmentsPageable`, `getAttachmentDefinitions`, `getAllowedFileExtensions`,
`getAttachmentStats`, `saveAttachmentDefinition`, `deleteAttachmentDefinition` (P).
Uploads and downloads are the REST endpoints in §1.

### Audit (`audit`)
`getMyActivityPageable` (A) · `getAuditLogsPageable`, `getRecordHistory`,
`getAuditedEntities`, `getAuditStats`, `exportAuditLogs`,
`getEndpointLogsPageable`, `resolveEndpointError`,
`resolveSimilarEndpointErrors`, `reopenEndpointError`,
`getErrorReportRecipients`, `setErrorReportRecipients`, `sendErrorReportNow` (P).

### Runtime settings (`configuration`)
`getPublicSettings` (public — name, logo, support contact for the sign-in page) ·
`getSystemSettings`, `updateSystemSetting`, `resetSystemSetting` (P).

### Workflows (`workflows`, optional)
Design (P): `getWorkflowsPageable`, `getWorkflow`, `getWorkflowActionCatalog`,
`getWorkflowTemplates`, `createWorkflow`, `createWorkflowFromTemplate`,
`updateWorkflow`, `saveWorkflowDesign`, `publishWorkflow`,
`revertWorkflowToDraft`, `archiveWorkflow`, `deleteWorkflow`.
Runtime (A — the transition's roles decide): `getEntityWorkflowState`,
`startWorkflow`, `executeWorkflowAction` · `getWorkflowItemsPageable`,
`getWorkflowEventsPageable` (P).

### Single sign-on provider (`sso`, optional)
`getMyConnectedSystems`, `revokeMyConnectedSystem` (A) · `getClientSystem`,
`getClientSystemsPageable`, `createClientSystem`, `updateClientSystem`,
`setClientSystemStatus`, `rotateClientSystemSecret`, `deleteClientSystem` (P).

### Example app (`example`, removed on init)
`getAnnouncements` (A), `getMyAnnouncementsPageable`, `saveAnnouncement`,
`submitAnnouncement`, `withdrawAnnouncement`, `decideAnnouncement`,
`deleteAnnouncement`, `getAllAnnouncementsPageable` (P).

## 5. Files over HTTP

Upload (multipart): `file`, `file_type` (`Document` | `Image`), optional
`purpose` (default `general`), `definition_uid`, `entity_type`, `entity_uid`.
Upload (JSON): `{ "file_type": "Image", "base64_string": "…", "file_name": "me.png" }`.
Answer `201`: `{ success, uid, file_path, url, name, mime, size, purpose, is_public }`.

Refusals `400` with a code: `9180` too large, `9181` type not accepted, `9182`
content does not match the extension, `9183` refused by the scanner, `9186`
scanner unreachable, `9184` may not publish public files.

Downloads answer `404` to anyone the file's policy does not admit — the same as
for a file that does not exist.

## 6. Limits and errors

A document deeper than `GRAPHQL_MAX_DEPTH`, costlier than
`GRAPHQL_MAX_COMPLEXITY`, with more than `GRAPHQL_MAX_ALIASES` aliases or
`GRAPHQL_MAX_ROOT_FIELDS` root fields, or a batch larger than
`GRAPHQL_MAX_BATCH_SIZE` (batching is off by default) is refused before execution
with `400` and a message naming the limit.

Rate limits answer code `9018` (GraphQL) or HTTP `429` (REST). Defaults: 30
sign-ins / 5 min per address, 5 registrations / 5 min, 6 OTPs / 10 min, 5 reset
requests / 15 min, 120 public-file lookups / min.

## 7. Keeping the schema honest

`docs/schema.graphql` is regenerated by `make schema` and a test fails when it is
stale — so every API change shows up as a diff in review. Clients can generate
types from it.
