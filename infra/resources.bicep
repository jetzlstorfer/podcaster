@description('Name of the azd environment.')
param environmentName string
param location string = resourceGroup().location
param tags object = {}

param foundryProjectEndpoint string = ''
param researcherAgentName string = ''
param scriptwriterAgentName string = ''
param narratorAgentName string = ''

@description('Chat model deployment used by the cover-art art director.')
param foundryModel string = 'gpt-5-mini'

@description('MAI image deployment used to render the cover art.')
param foundryImageModel string = 'MAI-Image-2.5-Flash'

param authClientId string = ''
@secure()
param authClientSecret string = ''

@description('Regional Azure Speech TTS endpoint. Empty = narrator skipped on Azure.')
param azureSpeechEndpoint string = ''

@description('ARM resource ID of the Speech account used for Entra ID TTS auth (aad#<id>#<token>).')
param azureSpeechResourceId string = ''

@description('Blob container for generated podcast audio (private).')
param audioContainerName string = 'audio'

@description('Target port the FastAPI backend listens on inside the container. Set to 80 so the initial placeholder image (containerapps-helloworld, which serves :80) yields a healthy revision; azd deploy keeps this port for the real image via WEB_PORT.')
param containerTargetPort int = 80

// Short, deterministic suffix for globally-unique resource names.
var resourceToken = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var prefix = 'pod${resourceToken}'

// Built-in role definition IDs.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var blobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-law'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appi'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------------------
// Storage — private container for generated MP3s (public access disabled)
// ---------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('${prefix}stor', 24)
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource audioContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: audioContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// Container registry (azd pushes the web image here)
// ---------------------------------------------------------------------------
resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: take('${prefix}acr', 50)
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

// ---------------------------------------------------------------------------
// Container Apps environment + the public backend/UI app
// ---------------------------------------------------------------------------
resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var enableAuth = !empty(authClientId)

// User-assigned identity for the web app. Using a user-assigned (not system-
// assigned) identity lets us grant AcrPull BEFORE the container app is created,
// so the first revision can validate the ACR registry credential instead of
// dead-locking (system MI principal only exists after the app is created).
resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-web-id'
  location: location
  tags: tags
}

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-web'
  location: location
  // The azd containerapp target matches services by this tag.
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${webIdentity.id}': {}
    }
  }
  // Make sure the identity has AcrPull/Blob roles before the first revision
  // validates the ACR registry credential.
  dependsOn: [
    acrPull
    blobReader
  ]
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: containerTargetPort
        transport: 'auto'
        // Long SSE runs (Option A): keep sessions unpinned and rely on the
        // stream staying active. Container Apps holds the connection while
        // bytes flow.
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: webIdentity.id
        }
      ]
      secrets: enableAuth ? [
        {
          name: 'auth-client-secret'
          value: authClientSecret
        }
      ] : []
    }
    template: {
      containers: [
        {
          // azd replaces this placeholder with the built image on `azd deploy`.
          name: 'web'
          image: placeholderImage
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'WEB_HOST', value: '0.0.0.0' }
            { name: 'WEB_PORT', value: string(containerTargetPort) }
            // Tells DefaultAzureCredential which user-assigned identity to use.
            { name: 'AZURE_CLIENT_ID', value: webIdentity.properties.clientId }
            { name: 'LOG_LEVEL', value: 'INFO' }
            { name: 'ENABLE_OTEL', value: 'true' }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            // Cover-art branch calls the model + MAI images REST API directly
            // (not via a hosted agent), so it needs the deployment names.
            { name: 'FOUNDRY_MODEL', value: foundryModel }
            { name: 'FOUNDRY_IMAGE_MODEL', value: foundryImageModel }
            { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
            { name: 'AZURE_STORAGE_CONTAINER', value: audioContainerName }
            { name: 'RESEARCHER_AGENT_NAME', value: researcherAgentName }
            { name: 'SCRIPTWRITER_AGENT_NAME', value: scriptwriterAgentName }
            { name: 'NARRATOR_AGENT_NAME', value: narratorAgentName }
            { name: 'AZURE_SPEECH_ENDPOINT', value: azureSpeechEndpoint }
            { name: 'AZURE_SPEECH_RESOURCE_ID', value: azureSpeechResourceId }
            { name: 'USE_SPEECH_ENTRA_AUTH', value: 'true' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// Entra Easy Auth in front of the Container App (optional).
resource webAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (enableAuth) {
  parent: web
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
          clientId: authClientId
          clientSecretSettingName: 'auth-client-secret'
        }
        validation: {
          allowedAudiences: [
            'api://${authClientId}'
          ]
        }
      }
    }
    login: {
      preserveUrlFragmentsForLogins: true
    }
  }
}

// ---------------------------------------------------------------------------
// RBAC — the backend's user-assigned managed identity
// ---------------------------------------------------------------------------
// Pull the web image from ACR.
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, webIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Read (stream) generated MP3s from the private container for the /audio proxy.
resource blobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, webIdentity.id, blobDataReaderRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataReaderRoleId)
    principalId: webIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output registryLoginServer string = registry.properties.loginServer
output webUri string = 'https://${web.properties.configuration.ingress.fqdn}'
output storageAccountUrl string = storage.properties.primaryEndpoints.blob
output audioContainerName string = audioContainerName
output webIdentityPrincipalId string = webIdentity.properties.principalId
output webIdentityClientId string = webIdentity.properties.clientId
