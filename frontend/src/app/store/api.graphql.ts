// The operations used by the migrated AlgoDial pages; checked against Django in CI.
export const CHANGE_MY_PASSWORD = `mutation C($old:String!,$next:String!,$confirm:String!){changeMyPassword(input:{oldPassword:$old,newPassword:$next,confirmPassword:$confirm}){status code message}}`;
export const CONFIRM_MFA = `mutation M($otp:String!){confirmMfaEnrollment(otp:$otp){status code message data{recoveryCodes}}}`;
export const BEGIN_MFA = `mutation{beginMfaEnrollment{status code message data{secret}}}`;
export const ACCOUNT_REQUIREMENTS = `query{meQuery{status code message data{passwordChangeRequired mfaEnrolmentRequired}}}`;
export const EXPORT_REPORT = `query E($section:String!,$format:TableExportFormatChoices!,$period:String,$since:Date,$until:Date){exportReport(section:$section,format:$format,period:$period,since:$since,until:$until){status code message data{fileName mimeType contentBase64}}}`;

export const END_AGENT_SESSION = `mutation {endAgentSession {status code message}}`;
export const END_SUPERVISOR_SESSION = `mutation {endSupervisorSession {status code message}}`;
