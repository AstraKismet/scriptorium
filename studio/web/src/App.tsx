/**
 * The shell, the boot gate, and the route switch.
 *
 * **The gate is the whole reason `contract_version` exists.** A client reads it
 * at startup and refuses a number it does not know — a hard stop, not a
 * degradation, because a client that half-works against a surface which has
 * moved underneath it fails silently and a reviewer finds out by losing wording.
 */
import { useEffect } from 'react'

import { Backends } from './components/Backends'
import { Confirm } from './components/Confirm'
import { Ledger } from './components/Ledger'
import { LogDrawer } from './components/LogDrawer'
import { Margin } from './components/Margin'
import { Rail } from './components/Rail'
import { Reading } from './components/Reading'
import { RoutingScreen } from './components/RoutingScreen'
import { Toolbar } from './components/Toolbar'
import { CONTRACT_VERSION } from './contract'
import * as routes from './router'
import { useStore } from './store'

export function App() {
  const boot = useStore(s => s.boot)
  const bootstrap = useStore(s => s.bootstrap)

  useEffect(() => { void bootstrap() }, [bootstrap])

  if (boot === 'loading') {
    return <div className="empty-page"><p>reading the project…</p></div>
  }

  if (boot === 'failed') {
    return <Refusal title="The workbench could not read this project" body={<FailedBody />} />
  }

  if (boot === 'incompatible') {
    return <Refusal title="This workbench does not know this server" body={<IncompatibleBody />} />
  }

  return (
    <div className="shell">
      <Rail />
      <Main />
      <Confirm />
    </div>
  )
}

function Refusal({ title, body }: { title: string; body: React.ReactNode }) {
  return (
    <div className="empty-page">
      <h3>{title}</h3>
      {body}
    </div>
  )
}

function FailedBody() {
  const why = useStore(s => s.bootError)
  const retry = useStore(s => s.bootstrap)
  return (
    <>
      <p>{why}</p>
      {/*
        The most likely cause is not a missing project, and saying so is the
        difference between a person fixing this in a minute and reinstalling
        something. `GET /api/state` raises whole on a `providers` block it cannot
        read — no `docs`, no `cwd`, not even a `contract_version` — so a
        hand-edited configuration takes the bootstrap down, and the screen that
        would repair it is behind the bootstrap.

        It is deliberately **not** answered by opening a blind settings form:
        that screen draws from the projection this request failed to return, so
        it would be a form with nothing in it offering to write keys nobody can
        see. A terminal can read the file; this page cannot.
      */}
      <p>
        A configuration this build cannot read takes this whole request down, so the
        likeliest cause is <code>lx.config.json</code> rather than a missing project.
        Run <code>lx providers</code> in the same directory — it names the block it
        could not read — and fix it with <code>lx config set</code> or in the file.
      </p>
      <p>
        <code>lx web</code> serves this page from the directory it was started in. If that
        directory has no <code>lx.config.json</code>, run <code>lx init</code> there first.
      </p>
      <p>
        <button type="button" onClick={() => void retry()}>Try again</button>
      </p>
    </>
  )
}

function IncompatibleBody() {
  const theirs = useStore(s => s.serverContract)
  return (
    <>
      <p>
        This page is written against contract version <b>{CONTRACT_VERSION}</b>. The server
        reports <b>{theirs}</b>.
      </p>
      <p>
        Refusing is deliberate. The versioned surface exists so that a client can stop
        rather than half-work: a page reading a key that has changed meaning does not fail
        where you can see it, it fails where a sentence goes missing.
      </p>
      <p>
        Rebuild the workbench from <code>studio/web/</code> and commit the output, or run a{' '}
        <code>lx</code> that matches this page.
      </p>
    </>
  )
}

function Main() {
  const route = routes.useRoute()
  const at = useStore(s => s.at)
  const doc = useStore(s => s.doc)
  const docLoading = useStore(s => s.docLoading)
  const docError = useStore(s => s.docError)
  const open = useStore(s => s.open)
  const focused = useStore(s => s.focused)
  const setFocused = useStore(s => s.setFocused)

  const addressed = route.name === 'doc' || route.name === 'read' ? route : null

  // The address is what decides which document is open. Nothing else calls
  // `open`, so the back button, a deep link and a click in the rail are one
  // path rather than three.
  useEffect(() => {
    if (!addressed) return
    if (at && at.src === addressed.src && at.lang === addressed.lang) return
    void open(addressed.src, addressed.lang)
  }, [addressed?.src, addressed?.lang, at, open, addressed])

  // The paragraph rides in the address so a reload lands where the reviewer was
  // and the ledger↔reading round trip comes back to the same place. Written with
  // `replace`, because a history entry per row would make the back button walk a
  // chapter one paragraph at a time.
  useEffect(() => {
    if (!addressed) return
    if (addressed.seg && addressed.seg !== focused) setFocused(addressed.seg)
  }, [addressed?.seg, addressed, focused, setFocused])

  useEffect(() => {
    if (route.name !== 'doc') return
    if (focused === route.seg) return
    routes.replace(routes.doc(route.src, route.lang, focused))
  }, [route, focused])

  if (route.name === 'backends') return <main><Backends /></main>
  if (route.name === 'routing') return <main><RoutingScreen /></main>

  if (!addressed) {
    return (
      <main>
        <div className="empty-page">
          <h3>Pick a document</h3>
          <p>
            Nothing is loaded yet. Choose one on the left, or extract a source file to start
            tracking it.
          </p>
        </div>
        <LogDrawer />
      </main>
    )
  }

  if (docError) {
    return (
      <main>
        <div className="empty-page">
          <h3>{addressed.src}</h3>
          <p>{docError}</p>
        </div>
        <LogDrawer />
      </main>
    )
  }

  if (!doc || docLoading) {
    return (
      <main>
        <div className="empty-page"><p>reading {addressed.src}…</p></div>
        <LogDrawer />
      </main>
    )
  }

  if (route.name === 'read') {
    return (
      <main>
        <Reading seg={route.seg} />
        <LogDrawer />
      </main>
    )
  }

  return (
    <main>
      <Toolbar />
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <Ledger />
        <Margin />
      </div>
      <LogDrawer />
    </main>
  )
}
