"""Server-owned verification-document contract constants."""

AGE_PROOF_DOCUMENT_TYPE = "age_proof"
GOVERNMENT_ID_DOCUMENT_TYPE = "government_id"
SUPPORTED_VERIFICATION_DOCUMENT_TYPES = (
    (AGE_PROOF_DOCUMENT_TYPE, "Age proof"),
    (GOVERNMENT_ID_DOCUMENT_TYPE, "Government ID"),
)

# Changing this is a policy release and must be coordinated with Flutter.
AGE_PROOF_CONSENT_POLICY_VERSION = "age-proof-v1"
