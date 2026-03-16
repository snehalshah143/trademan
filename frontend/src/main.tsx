import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import { AdapterProvider } from '@adapters/AdapterContext'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AdapterProvider>
        <BrowserRouter>
          <ErrorBoundary>
          <App />
          <Toaster
            position="bottom-right"
            toastOptions={{
              style: {
                background: '#18181c',
                color: '#e8e8f0',
                border: '1px solid #2a2a36',
                fontSize: '13px',
              },
            }}
          />
          </ErrorBoundary>
        </BrowserRouter>
      </AdapterProvider>
    </QueryClientProvider>
  </React.StrictMode>
)
