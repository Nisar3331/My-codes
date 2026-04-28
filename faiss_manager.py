# Authentication Methods for Polling Service

The polling service supports multiple authentication methods for each cloud platform. Configure auth in your encrypted `config/integrations.enc` under each instance's `auth` field.

## AWS Glue (CloudWatch Logs)

### 1. Default (Recommended)
Uses AWS SDK credential chain: environment variables → instance profile → ~/.aws/credentials → AWS SSO.

```yaml
integrations:
  aws_glue:
    - name: prod-glue
      region: us-east-1
      auth:
        method: default
      log_group: /aws-glue/prod-etl
```

### 2. Named Profile
Uses a specific AWS profile from ~/.aws/credentials or ~/.aws/config.

```yaml
auth:
  method: profile
  profile_name: prod-account
```

### 3. Cross-Account Role (Assume Role)
Assume a role in another account via STS. Useful for centralized polling account.

```yaml
auth:
  method: assume_role
  role_arn: arn:aws:iam::123456789012:role/GluePollingRole
  session_name: self-healing-poller
  duration_seconds: 3600
```

### 4. Explicit Credentials
Not recommended for production. Credentials stored in encrypted config only.

```yaml
auth:
  method: explicit
  access_key_id: AKIAIOSFODNN7EXAMPLE
  secret_access_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

---

## Azure Data Factory / Azure Databricks (Log Analytics)

### 1. Default (Recommended)
Uses DefaultAzureCredential: environment variables → managed identity → CLI → interactive.

```yaml
integrations:
  azure_data_factory:
    - name: prod-adf
      subscription_id: 12345678-1234-1234-1234-123456789012
      tenant_id: 87654321-4321-4321-4321-210987654321
      auth:
        method: default
      meta:
        resource_group: prod-rg
        factory_name: prod-adf-factory
```

### 2. Managed Identity
Use a managed identity (VM, App Service, AKS, etc.). Optional: specify `client_id` if multiple identities exist.

```yaml
auth:
  method: managed_identity
  client_id: f47ac10b-58cc-4372-a567-0e02b2c3d479  # Optional
```

### 3. Service Principal (Client Secret)
Authenticate as a service principal using client credentials.

```yaml
auth:
  method: client_secret
  tenant_id: 87654321-4321-4321-4321-210987654321
  client_id: f47ac10b-58cc-4372-a567-0e02b2c3d479
  client_secret: your-client-secret-here
```

---

## Airflow (Self-Hosted, MWAA, Cloud Composer)

### 1. Self-Hosted Airflow
JWT-based authentication for self-managed Airflow instances.

```yaml
integrations:
  airflow:
    - name: prod-airflow
      variant: self_hosted
      base_url: https://airflow.prod.example.com
      auth:
        method: jwt_password
        username: airflow_admin
        password: your-password-here
```

### 2. Amazon MWAA (Managed Workflows for Apache Airflow)
Uses boto3 `mwaa.invoke_rest_api` for secure access to MWAA environment.

```yaml
integrations:
  airflow:
    - name: prod-mwaa
      variant: mwaa
      environment_name: prod-mwaa-env
      region: us-east-1
      auth:
        method: aws_invoke_rest_api
```
Requires AWS credentials (see AWS section above for auth method options).

### 3. Cloud Composer (Google Cloud Composer)
IAP (Identity-Aware Proxy) protected Airflow with OIDC token authentication.

```yaml
integrations:
  airflow:
    - name: prod-composer
      variant: composer
      base_url: https://example-region-composer.aip.c.composer.goog
      auth:
        method: iap_oidc
        audience: /projects/123456/global/backendServices/backend-service-id
```
Uses Application Default Credentials to get OIDC ID token for IAP.

---

## GCP Cloud Composer (Cloud Logging)

### 1. Application Default Credentials (Recommended)
Uses ADC: GOOGLE_APPLICATION_CREDENTIALS env var → gcloud auth → service account.

```yaml
integrations:
  gcp_composer:
    - name: prod-composer
      project_id: my-gcp-project
      composer_environment: prod-composer-env
      auth:
        method: default
```

### 2. Service Account Key
Explicit path to service account JSON key file.

```yaml
auth:
  method: service_account_key
  key_path: /secrets/gcp-service-account.json
```

---

## Best Practices

1. **Always use encrypted config**: Store all credentials in encrypted `config/integrations.enc`, never in plaintext or environment variables (except as bootstrap).

2. **Use managed identities where available**:
   - Azure: Managed Identity (VM, App Service, AKS, Container Instances)
   - GCP: Service Account (Compute Engine, GKE, Cloud Run)
   - AWS: IAM role (EC2 instance profile, ECS task role, Lambda execution role)

3. **Least privilege**: Create dedicated roles/service accounts with minimal permissions:
   - CloudWatch Logs query
   - Log Analytics query
   - Airflow REST API read access

4. **Rotate credentials regularly**: Implement credential rotation for client secrets, API keys, passwords.

5. **Audit access**: Monitor who/what is calling the polling service and accessing logs.

6. **Use short-lived tokens**: For assume_role (AWS) and OIDC (GCP Composer), set reasonable `duration_seconds` / token expiry.

---

## Troubleshooting

### AWS
- **"NoCredentialsError"**: Check AWS credential chain (env vars, ~/.aws/credentials, instance profile)
- **"AccessDenied"**: Ensure IAM role/user has `logs:QueryLogGroup`, `logs:GetQueryResults` permissions
- **"AssumeRoleUnauthorized"**: Ensure trust relationship allows the source principal

### Azure
- **"ClientAuthenticationError"**: Check service principal credentials (tenant_id, client_id, client_secret)
- **"AuthenticationFailed"**: Managed identity not available (not running on Azure resource)
- **"OperationNotPermitted"**: User/service principal lacks Log Analytics query permissions

### Airflow
- **Self-Hosted "Unauthorized"**: Verify username/password in `/auth/token` endpoint
- **MWAA "AccessDenied"**: Ensure AWS credentials can call `mwaa:InvokeRestApi`
- **Composer "Unauthorized"**: Check IAP authorization and audience value

### GCP
- **"DefaultCredentialsError"**: GOOGLE_APPLICATION_CREDENTIALS not set or invalid
- **"PermissionDenied"**: Service account lacks `logging.logEntries.list` permission
