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
import { CONTRACT_VERSION, type DocAddress } from './contract'
import * as drafts from './drafts'
import * as routes from './router'
import { notExtracted, useStore } from './store'

export function App() {
  const boot = useStore(s => s.boot)
  const bootstrap = useStore(s => s.bootstrap)

  useEffect(() => { void bootstrap() }, [bootstrap])

  // The unsaved-work guard, and it lives in the shell rather than in the toolbar
  // because the toolbar is not on every screen. The reading view, both backend
  // screens and the refusal `Main` draws when it will not leave a document are
  // all rendered without it — and a draft that failed to save survives into
  // every one of them, so the guard it was relying on simply was not there.
  // `beforeunload` is the browser's only hook and it cannot say what is
  // unsaved, but it is the difference between closing a tab and losing an
  // afternoon's wording. It is deliberately not the fragment navigations this
  // page does to itself: `beforeunload` does not fire for those at all, and
  // `store.open()` is where those are answered.
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => { if (drafts.size()) e.preventDefault() }
    window.addEventListener('beforeunload', guard)
    return () => { window.removeEventListener('beforeunload', guard) }
  }, [])

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
  const readFailed = useStore(s => s.readFailed)
  const state = useStore(s => s.state)
  const open = useStore(s => s.open)

  const addressed = route.name === 'doc' || route.name === 'read' ? route : null

  // The address is what decides which document is open, so the back button, a
  // deep link and every entry in the rail are one path rather than several.
  //
  // This effect turns the address into `at`, and nothing else writes `at`:
  // `open()` is its only writer, and its one other caller, `store.reExtract`,
  // calls it only for the document `at` already names, once a re-parse has
  // landed. That is a re-read after the toolbar's Re-extract, and the first
  // successful read on the page for a file nobody had extracted, where `at`
  // named the file while `doc` stayed on the document before it. `at` outlives
  // a document route — on `#/backends` this returns early and `at` keeps the
  // last document — which is harmless and is why the sentence is about `at`
  // rather than the address.
  //
  // It was false twice before it was this: a comment here said nothing else
  // called `open` while `Rail`'s *Not yet extracted* entry did, beside the
  // address, and lost its document to this effect a render later. Since
  // HANDOFF-088 that entry is a link like every other. `open()` still writes
  // unsaved words out itself rather than leaving it to this effect, because
  // leaving a document is what costs them and not who asked — the rule holds
  // for a caller nobody has written yet.
  useEffect(() => {
    if (!addressed) return
    if (at && at.src === addressed.src && at.lang === addressed.lang) return
    void open(addressed.src, addressed.lang)
  }, [addressed?.src, addressed?.lang, at, open, addressed])

  // **There is no effect here for the segment, and one must not be added.** The
  // paragraph a reviewer is on is the address's `?seg=` and nothing else: a row
  // or a paragraph writes it with `routes.focus`, and every reader takes it from
  // the address, through `routes.useFocused` or `routes.useIsFocused`. Two
  // effects used to keep a copy in the store in step with it, one in each
  // direction, and the first time both named a segment and the two differed
  // they overwrote each other until React unmounted the page — on the first
  // click that moved to a different segment (HANDOFF-084).

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
          {/*
            Two facts, and both are needed. The read has just failed — fresh,
            and about this one file — and the project lists the file as not yet
            extracted, which is a snapshot a terminal can have made stale. A
            stale entry for a file somebody has since extracted never gets here:
            its read succeeds and the document opens. And a page that declined
            to leave another document over words it could not write is not a
            failed read, so it offers nothing.
          */}
          {readFailed && notExtracted(state, addressed) && (
            <NotExtracted src={addressed.src} lang={addressed.lang} />
          )}
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
        <Reading />
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

/**
 * The extract, offered on the page of the file it is about.
 *
 * The rail's *Not yet extracted* entry leads here and does nothing else, and so
 * does a hand-typed link to such a file — one page, whichever way a reviewer
 * arrived. The request names the file this page is about and nothing on the
 * screen, and `reExtract` then opens it, because `at` names it.
 *
 * **No confirmation, and that is decided rather than inherited.** The toolbar's
 * asks only when a re-parse could discard a translation, and this is drawn only
 * after a read of this very file has just failed while the project lists it as
 * never extracted — so there is no translation, hold or waiver to discard, and
 * the document on screen before this one is not touched. What is left is the
 * interval between that read and the click, which a terminal could fill; the
 * click would then be a plain re-extract, which keeps every translation whose
 * paragraph is unchanged — the act `lx run` performs on every invocation.
 */
function NotExtracted({ src, lang }: DocAddress) {
  const running = useStore(s => s.running)
  const extract = useStore(s => s.extract)
  const say = useStore(s => s.say)
  return (
    <>
      <p>
        This file matches the project&rsquo;s <code>sources</code> and has not been extracted into{' '}
        {lang}. Extracting reads it and parses it into segments; wording already banked in the
        translation memory is offered to them, and nothing that is tracked is touched.
      </p>
      <p>
        <button
          type="button"
          className="key"
          disabled={running}
          title={running ? 'Waits for the run in flight: one act at a time.' : undefined}
          onClick={() => {
            // Read now rather than from the render: a click in the same frame as
            // the one that started a run would otherwise log a header over a
            // request `reExtract` then declines to send.
            if (useStore.getState().running) return
            say(`— extract ${src} [${lang}] —`, 'plain', true)
            void extract({ src, lang })
          }}
        >
          Extract {src}
        </button>
      </p>
    </>
  )
}
