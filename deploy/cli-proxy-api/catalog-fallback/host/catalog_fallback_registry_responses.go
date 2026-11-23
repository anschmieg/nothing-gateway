package registry

import (
	"strings"
	"time"
)

const ResponsesPluginID = "openai-responses"

const responsesPluginClientID = "plugin:" + ResponsesPluginID + ":" + ResponsesPluginID + ":executor"

func (r *ModelRegistry) responsesPluginModelLocked(model string) bool {
	return strings.HasPrefix(model, ResponsesPluginID+"/") &&
		r.clientProviders[responsesPluginClientID] == ResponsesPluginID &&
		r.clientModelInfos[responsesPluginClientID][model] != nil
}

// ResponsesPluginRouteAvailable checks the exact model registered by the static executor.
func (r *ModelRegistry) ResponsesPluginRouteAvailable(model string) bool {
	r.mutex.RLock()
	defer r.mutex.RUnlock()
	if !r.responsesPluginModelLocked(model) {
		return false
	}
	return r.canonicalFallbackRegistryAvailableLocked(responsesPluginClientID, model, time.Now())
}
