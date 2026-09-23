export function modelTermsFixture(modelId = 'model', overrides = {}) {
  return {
    modelId, name: 'Fixture model', recordValid: true, errors: [], releaseReady: false,
    termsDigest: 'a'.repeat(64),
    terms: {
      sources: [{ repository: 'publisher/model', revision: 'b'.repeat(40), role: 'artifact_publisher',
        url: 'https://huggingface.co/publisher/model/tree/revision',
        declaration_url: 'https://huggingface.co/publisher/model/blob/revision/README.md',
        license_id: 'other', license_name: 'Publisher model terms', license_url: 'https://publisher.example/terms' }],
      license_documents: [{ repo: 'publisher/model', url: 'https://publisher.example/license' }],
      notice_documents: [{ repository: 'publisher/model', path: 'NOTICE', url: 'https://publisher.example/notice' }],
      commercial_use: 'not_assessed', upstream_acceptance: 'not_assessed',
      declared_base_repositories: ['upstream/base'],
      issues: ['BASE_TERMS_NOT_REVIEWED'], note: 'Check the conditions for the intended use.',
      artifacts: [{ observed_present: true }],
    },
    ...overrides,
  }
}
