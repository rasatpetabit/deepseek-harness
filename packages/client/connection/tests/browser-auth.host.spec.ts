/** Browser launch-token and persistent-cookie behavior. */

import { createHmac } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CredentialProvider } from '@deepseek-ai/dsh-credentials'
import { BrowserAuth } from '../src/browser-auth.ts'
import type { ConnectionIndexRequest, ConnectionIndexResponse } from '../src/rpc.ts'
import { RecordCredentials } from './browser-credentials.ts'

function signedCookie(store: RecordCredentials, name: string, payload: unknown): string {
  const body = typeof payload === 'string'
    ? Buffer.from(payload, 'utf8').toString('base64url')
    : Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url')
  return signedBodyCookie(store, name, body)
}

function signedBodyCookie(store: RecordCredentials, name: string, body: string): string {
  const record = store.record
  if (record?.kind !== 'grant' || typeof record.payload !== 'object' || record.payload === null) {
    throw new Error('test credential store has no signing secret')
  }
  const secret: unknown = Reflect.get(record.payload, 'secret')
  if (typeof secret !== 'string') throw new Error('test credential record has no string secret')
  const signature = createHmac('sha256', Buffer.from(secret, 'base64url')).update(body).digest('base64url')
  return `${name}=v1.${body}.${signature}`
}

interface ResponseState {
  status?: number
  headers?: Readonly<Record<string, string>>
  body?: string
}

function response(): { value: ConnectionIndexResponse; state: ResponseState } {
  const state: ResponseState = {}
  return {
    value: {
      writeHead(status, headers) {
        state.status = status
        if (headers !== undefined) state.headers = headers
      },
      end(body) {
        if (body !== undefined) state.body = body
      },
    },
    state,
  }
}

function credentials(store: RecordCredentials): CredentialProvider {
  return store as unknown as CredentialProvider
}

function createAuth(
  store: RecordCredentials,
  maxAgeDays = 30,
  processOwner: object = {},
): Promise<BrowserAuth> {
  return BrowserAuth.create(processOwner, credentials(store), maxAgeDays)
}

function request(url: string, authority = '127.0.0.1:3080', init?: {
  cookie?: string
  method?: string
}): ConnectionIndexRequest {
  return {
    method: init?.method ?? 'GET',
    url,
    headers: {
      host: authority,
      ...init?.cookie === undefined ? {} : { cookie: init.cookie },
    },
  }
}

function exchange(
  auth: BrowserAuth,
  authority = '127.0.0.1:3080',
): { cookie: string; launchUrl: string; state: ResponseState } {
  const launchUrl = auth.authenticatedUrl(`http://${authority}`)
  const target = new URL(launchUrl)
  const res = response()
  expect(auth.authorizeIndex(request(`${target.pathname}${target.search}`, authority), res.value)).toBe(false)
  const setCookie = res.state.headers?.['set-cookie']
  if (setCookie === undefined) throw new Error('token exchange did not set a cookie')
  return { cookie: setCookie.split(';', 1)[0]!, launchUrl, state: res.state }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('BrowserAuth', () => {
  it('mints one process token and a persistent authority-bound cookie', async () => {
    const store = new RecordCredentials()
    const processOwner = {}
    const first = await createAuth(store, 30, processOwner)
    const login = exchange(first)

    expect(login.state).toMatchObject({
      status: 303,
      headers: {
        'cache-control': 'no-store',
        'location': './',
        'referrer-policy': 'no-referrer',
      },
    })
    expect(login.state.headers?.['set-cookie']).toMatch(/; Max-Age=2592000; Path=\/; Expires=.*; HttpOnly; SameSite=Strict$/u)
    expect(login.state.headers?.['set-cookie']).not.toContain('Secure')
    expect(first.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: login.cookie }))).toBe(true)
    expect(first.isAuthenticated({
      headers: new Headers({ host: '127.0.0.1:3080', cookie: login.cookie }),
    })).toBe(true)
    expect(first.isAuthenticated({ headers: new Headers() })).toBe(false)
    expect(first.isAuthenticated(request('/', 'localhost:3080', { cookie: login.cookie }))).toBe(false)
    expect(first.isAuthenticated(request('/', '127.0.0.1:3081', { cookie: login.cookie }))).toBe(false)

    const reloaded = await createAuth(store, 30, processOwner)
    expect(reloaded.authenticatedUrl('http://127.0.0.1:3080')).toBe(login.launchUrl)
    expect(reloaded.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: login.cookie }))).toBe(true)

    const restarted = await createAuth(store)
    expect(new URL(restarted.authenticatedUrl('http://127.0.0.1:3080')).searchParams.get('token'))
      .not.toBe(new URL(login.launchUrl).searchParams.get('token'))
    expect(restarted.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: login.cookie }))).toBe(true)
    const staleUrl = new URL(login.launchUrl)
    const redirected = response()
    expect(restarted.authorizeIndex(request(
      `${staleUrl.pathname}${staleUrl.search}`,
      '127.0.0.1:3080',
      { cookie: login.cookie },
    ), redirected.value)).toBe(false)
    expect(redirected.state).toEqual({
      status: 303,
      headers: {
        'cache-control': 'no-store',
        'location': './',
        'referrer-policy': 'no-referrer',
      },
    })
  })

  it('preserves the caller authority and mount while adding only this process token', async () => {
    const auth = await createAuth(new RecordCredentials())
    const mounted = new URL(auth.authenticatedUrl('https://gateway.example/tools/dsh/'))
    expect(mounted.origin).toBe('https://gateway.example')
    expect(mounted.pathname).toBe('/tools/dsh/')
    expect([...mounted.searchParams.keys()]).toEqual(['token'])

    const loopback = new URL(auth.authenticatedUrl('http://127.0.0.1:3080/'))
    expect(loopback.origin).toBe('http://127.0.0.1:3080')
    expect(loopback.pathname).toBe('/')
    expect(loopback.searchParams.get('token')).toBe(mounted.searchParams.get('token'))

    // The proxy preserves the browser-facing Host and strips the mount.
    const token = mounted.searchParams.get('token')
    const exchanged = response()
    expect(auth.authorizeIndex(request(`/?token=${String(token)}`, 'gateway.example'), exchanged.value)).toBe(false)
    const setCookie = exchanged.state.headers?.['set-cookie']
    if (setCookie === undefined) throw new Error('mount exchange did not set a cookie')
    expect(auth.isAuthenticated(request(
      '/', 'gateway.example', { cookie: setCookie.split(';', 1)[0]! },
    ))).toBe(true)
  })

  it('accepts the cookie for index serving and gives every unauthenticated request one response', async () => {
    const auth = await createAuth(new RecordCredentials())
    const { cookie } = exchange(auth)
    const allowed = response()
    expect(auth.authorizeIndex(request('/index.html', '127.0.0.1:3080', { cookie }), allowed.value)).toBe(true)
    expect(allowed.state).toEqual({})

    for (const candidate of [
      request('/'),
      request('/?token=wrong'),
      request('/?token=wrong&token=again'),
      request('/index.html?token=wrong'),
      request(auth.authenticatedUrl('http://127.0.0.1:3080'), '127.0.0.1:3080', { method: 'HEAD' }),
    ]) {
      const denied = response()
      expect(auth.authorizeIndex(candidate, denied.value)).toBe(false)
      expect(denied.state.status).toBe(401)
      expect(denied.state.headers).toEqual({
        'cache-control': 'no-store',
        'content-type': 'text/plain; charset=utf-8',
      })
      expect(denied.state.body).toBe(candidate.method === 'HEAD'
        ? undefined
        : 'dsh web authentication required; reopen the URL printed by dsh web.\n')
    }
  })

  it('rejects tampering, expiry, future issuance, and a longer lifetime than configured', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-24T00:00:00.000Z'))
    const store = new RecordCredentials()
    const auth = await createAuth(store)
    const { cookie } = exchange(auth)
    const [name, value] = cookie.split('=') as [string, string]

    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: `${name}=broken` }))).toBe(false)
    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: `${name}=${value.slice(0, -1)}x` }))).toBe(false)
    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: `${name}=%` }))).toBe(false)
    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', {
      cookie: signedBodyCookie(store, name, 'a'),
    }))).toBe(false)
    expect(auth.isAuthenticated({ headers: {} })).toBe(false)
    expect(auth.isAuthenticated({ headers: { host: 'bad host', cookie } })).toBe(false)
    expect(auth.isAuthenticated({ headers: { host: '127.0.0.1:3080' } })).toBe(false)

    const invalidPayloads: unknown[] = [
      'not json',
      null,
      { version: 2, authority: '127.0.0.1:3080', issuedAt: Date.now(), expiresAt: Date.now() + 1000 },
      { version: 1, authority: 42, issuedAt: Date.now(), expiresAt: Date.now() + 1000 },
      { version: 1, authority: '127.0.0.1:3080', issuedAt: 'now', expiresAt: Date.now() + 1000 },
      { version: 1, authority: '127.0.0.1:3080', issuedAt: Date.now(), expiresAt: 'later' },
    ]
    for (const payload of invalidPayloads) {
      expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', {
        cookie: signedCookie(store, name, payload),
      }))).toBe(false)
    }

    const shorter = await createAuth(store, 1)
    expect(shorter.isAuthenticated(request('/', '127.0.0.1:3080', { cookie }))).toBe(false)
    vi.setSystemTime(new Date('2026-09-24T00:00:00.000Z'))
    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', { cookie }))).toBe(false)
    vi.setSystemTime(new Date('2026-08-23T00:00:00.000Z'))
    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', { cookie }))).toBe(false)
  })

  it('loads one secret per activation and replaces it after deletion on the next activation', async () => {
    const store = new RecordCredentials()
    const auth = await createAuth(store)
    const first = exchange(auth)
    expect(store).toMatchObject({ reads: 0, modifies: 1 })

    await store.deleteRecord()
    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: first.cookie }))).toBe(true)
    const sameActivation = exchange(auth)
    expect(auth.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: sameActivation.cookie }))).toBe(true)
    expect(store).toMatchObject({ reads: 0, modifies: 1 })

    const reactivated = await createAuth(store)
    const second = exchange(reactivated)
    expect(second.cookie).not.toBe(first.cookie)
    expect(reactivated.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: first.cookie }))).toBe(false)
    expect(reactivated.isAuthenticated(request('/', '127.0.0.1:3080', { cookie: second.cookie }))).toBe(true)
    expect(store).toMatchObject({ reads: 0, modifies: 2 })
  })

  it('fails loud on an invalid owner record instead of replacing it', async () => {
    const unsupported = new RecordCredentials()
    unsupported.record = { kind: 'api-key', key: 'not-a-cookie-secret' }
    await expect(createAuth(unsupported)).rejects.toThrow(/unsupported format/u)

    const malformed = new RecordCredentials()
    malformed.record = { kind: 'grant', payload: { version: 1, secret: 'short' } }
    await expect(createAuth(malformed)).rejects.toThrow(/invalid secret/u)

    const nonString = new RecordCredentials()
    nonString.record = { kind: 'grant', payload: { version: 1, secret: 42 } }
    await expect(createAuth(nonString)).rejects.toThrow(/invalid secret/u)

    const discarded = new RecordCredentials()
    discarded.discardWrites = true
    await expect(createAuth(discarded)).rejects.toThrow(/was not created/u)

    await expect(createAuth(new RecordCredentials(), Number.MAX_SAFE_INTEGER))
      .rejects.toThrow(/safe timestamp range/u)
  })

  it.each([
    ['GET', '/?x=1', '127.0.0.1'],
    ['GET', '/?', '127.0.0.1'],
    ['HEAD', '/', '127.0.0.1'],
    ['POST', '/', '127.0.0.1'],
    ['GET', '/index.html', '127.0.0.1'],
    ['GET', '/api', '127.0.0.1'],
    ['GET', '/?token=wrong', '127.0.0.1'],
    ['GET', '/', '10.0.0.1'],
    ['GET', '/', undefined],
  ])('does not auto-login %s %s from %s despite forwarded headers', async (method, url, remoteAddress) => {
    const auth = await BrowserAuth.create({}, credentials(new RecordCredentials()), 30, ['127.0.0.1'])
    const res = response()
    expect(auth.authorizeIndex({
      method, url,
      headers: {
        host: '192.168.100.200',
        forwarded: 'for=192.168.100.42',
        'x-forwarded-for': '127.0.0.1',
        'x-real-ip': '127.0.0.1',
      },
      socket: remoteAddress === undefined ? undefined : { remoteAddress },
    }, res.value)).toBe(false)
    expect(res.state.status).toBe(401)
    expect(res.state.headers?.['set-cookie']).toBeUndefined()
  })

  it('auto-login through the accepted loopback hop retains authority-bound cookies and credentials', async () => {
    const store = new RecordCredentials()
    const owner = {}
    const auth = await BrowserAuth.create(owner, credentials(store), 30, ['127.0.0.1'])
    const originalSecret = structuredClone(store.record)
    const res = response()
    const req = { method: 'GET', url: '/', headers: { host: '192.168.100.200' }, socket: { remoteAddress: '127.0.0.1' } }
    expect(auth.authorizeIndex(req, res.value)).toBe(false)
    expect(res.state.status).toBe(303)
    expect(res.state.headers?.location).toBe('./')
    const cookie = res.state.headers?.['set-cookie']?.split(';')[0]
    const authenticated = { ...req, headers: { ...req.headers, cookie } }
    expect(auth.authorizeIndex(authenticated, response().value)).toBe(true)
    expect(auth.isAuthenticated(authenticated)).toBe(true)
    expect(auth.isAuthenticated({ headers: { host: 'evil.example', cookie } })).toBe(false)
    const reopened = await BrowserAuth.create(owner, credentials(store), 30, ['127.0.0.1'])
    expect(reopened.isAuthenticated(authenticated)).toBe(true)
    expect(store.record).toEqual(originalSecret)
  })

  it('automatically authenticates requests from trusted networks on GET /', async () => {
    const store = new RecordCredentials()
    const auth = await BrowserAuth.create(
      {}, store as unknown as CredentialProvider, 30, ['192.168.100.0/24', '127.0.0.1'],
    )

    // 1. Request from trusted subnet without token mints cookie and redirects to /
    const trustedReq = {
      method: 'GET',
      url: '/',
      headers: { host: '192.168.100.177:3080' },
      socket: { remoteAddress: '192.168.100.42' },
    }
    const resHeaders: Record<string, string> = {}
    let resStatus = 0
    const res = {
      writeHead(status: number, headers?: Record<string, string>) {
        resStatus = status
        if (headers) Object.assign(resHeaders, headers)
      },
      end() {},
    }
    expect(auth.authorizeIndex(trustedReq, res)).toBe(false)
    expect(resStatus).toBe(303)
    expect(resHeaders['set-cookie']).toBeDefined()
    expect(resHeaders['set-cookie']).toContain('dsh-auth-')

    // 2. Following the redirect with the new cookie serves index (returns true)
    const cookieValue = resHeaders['set-cookie']?.split(';')[0]?.trim()
    const authedReq = {
      method: 'GET',
      url: '/',
      headers: { host: '192.168.100.177:3080', cookie: cookieValue },
      socket: { remoteAddress: '192.168.100.42' },
    }
    expect(auth.authorizeIndex(authedReq, res)).toBe(true)

    // 3. Request from untrusted subnet without token is rejected with 401
    const untrustedReq = {
      method: 'GET',
      url: '/',
      headers: { host: '192.168.100.177:3080' },
      socket: { remoteAddress: '10.0.0.1' },
    }
    resStatus = 0
    expect(auth.authorizeIndex(untrustedReq, res)).toBe(false)
    expect(resStatus).toBe(401)
  })
})
