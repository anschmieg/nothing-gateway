package auth

import (
	"context"
	"strings"
	"sync"
	"time"

	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
)

// A recovery attempt is demand-driven: no timer or background inference is used.
// Leases live only as long as the request (including a streaming response).
func (m *Manager) acquireGeminiRecovery(a *Auth, model string) (func(), error) {
	if a == nil || !strings.EqualFold(a.Provider, "gemini") {
		return nil, nil
	}
	m.geminiRecoveryMu.Lock()
	defer m.geminiRecoveryMu.Unlock()
	m.mu.RLock()
	live := m.auths[a.ID]
	now := time.Now()
	key := ""
	recovery := false
	if live != nil {
		if live.Quota.Reason == "credential_quota" && live.Quota.Exceeded && !live.Quota.NextRecoverAt.IsZero() {
			recovery = !live.Quota.NextRecoverAt.After(now)
			key = a.ID
		} else if state := existingModelState(live, canonicalModelKey(model)); state != nil && state.Unavailable && !state.NextRetryAfter.IsZero() {
			recovery = !state.NextRetryAfter.After(now)
			key = a.ID + ":" + canonicalModelKey(model)
		}
	}
	m.mu.RUnlock()
	if !recovery {
		return nil, nil
	}
	if m.geminiRecovery == nil {
		m.geminiRecovery = make(map[string]bool)
	}
	if m.geminiRecovery[key] {
		return nil, NewRequestScopedError("Gemini recovery attempt in progress; retry shortly", 503)
	}
	m.geminiRecovery[key] = true
	var once sync.Once
	return func() {
		once.Do(func() { m.geminiRecoveryMu.Lock(); delete(m.geminiRecovery, key); m.geminiRecoveryMu.Unlock() })
	}, nil
}

func holdGeminiRecoveryStream(ctx context.Context, result *cliproxyexecutor.StreamResult, release func()) *cliproxyexecutor.StreamResult {
	if release == nil {
		return result
	}
	if result == nil {
		release()
		return result
	}
	chunks := result.Chunks
	out := make(chan cliproxyexecutor.StreamChunk)
	copyResult := *result
	copyResult.Chunks = out
	go func() {
		defer close(out)
		defer release()
		for {
			select {
			case <-ctx.Done():
				return
			case chunk, ok := <-chunks:
				if !ok {
					return
				}
				select {
				case out <- chunk:
				case <-ctx.Done():
					return
				}
			}
		}
	}()
	return &copyResult
}
