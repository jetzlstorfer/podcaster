targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment — used to tag and name resources.')
param environmentName string

@minLength(1)
@description('Primary location for all resources.')
param location string = 'swedencentral'

@description('Existing resource group to deploy into (reused). Default: rg-podcaster.')
param resourceGroupName string = 'rg-podcaster'

@description('Foundry project endpoint the backend invokes hosted agents through.')
param foundryProjectEndpoint string = ''

@description('Hosted agent names the backend invokes (set after the agents are deployed).')
param researcherAgentName string = ''
param scriptwriterAgentName string = ''
param narratorAgentName string = ''

@description('Chat model deployment used by the cover-art art director.')
param foundryModel string = 'gpt-5-mini'

@description('MAI image deployment used to render the cover art.')
param foundryImageModel string = 'MAI-Image-2.5-Flash'

@description('Entra app (client) ID for Container Apps Easy Auth. Empty = Easy Auth disabled.')
param authClientId string = ''

@secure()
@description('Entra app client secret for Easy Auth (required when authClientId is set).')
param authClientSecret string = ''

@description('Regional Azure Speech TTS endpoint (e.g. https://<region>.tts.speech.microsoft.com/). Empty = narrator skipped.')
param azureSpeechEndpoint string = ''

@description('ARM resource ID of the Azure Speech account — required for Entra ID auth (aad#<id>#<token> format). E.g. /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>.')
param azureSpeechResourceId string = ''

var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'podcaster-resources'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    foundryProjectEndpoint: foundryProjectEndpoint
    researcherAgentName: researcherAgentName
    scriptwriterAgentName: scriptwriterAgentName
    narratorAgentName: narratorAgentName
    foundryModel: foundryModel
    foundryImageModel: foundryImageModel
    authClientId: authClientId
    authClientSecret: authClientSecret
    azureSpeechEndpoint: azureSpeechEndpoint
    azureSpeechResourceId: azureSpeechResourceId
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output SERVICE_WEB_ENDPOINT_URL string = resources.outputs.webUri
output AZURE_STORAGE_ACCOUNT_URL string = resources.outputs.storageAccountUrl
output AZURE_STORAGE_CONTAINER string = resources.outputs.audioContainerName
output WEB_IDENTITY_PRINCIPAL_ID string = resources.outputs.webIdentityPrincipalId
output WEB_IDENTITY_CLIENT_ID string = resources.outputs.webIdentityClientId
