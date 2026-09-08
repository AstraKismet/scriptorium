import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'
import './theme.css'

const root = document.getElementById('root')
if (!root) throw new Error('no #root — index.html and this entry disagree')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
