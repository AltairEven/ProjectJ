import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './style.css'

// No browser context menu on the page; typing fields keep it for paste and spelling.
document.addEventListener('contextmenu', event => {
  if (!(event.target instanceof Element && event.target.closest('textarea, input'))) event.preventDefault()
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>,
)
