import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { InputWorkspace } from './InputWorkspace'

const options = {
  venues: window.PCC_MOCK.venues,
  models: window.PCC_MOCK.models,
}

function renderWorkspace(onSubmit = vi.fn(), modelOptions = options.models) {
  render(
    <InputWorkspace
      options={{ ...options, models: modelOptions }}
      isSubmitting={false}
      requestError={null}
      scenario="completed"
      onScenarioChange={vi.fn()}
      onSubmit={onSubmit}
    />,
  )
  return onSubmit
}

describe('InputWorkspace', () => {
  it('explains why an ArXiv input was rejected instead of failing generically', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await user.type(screen.getByLabelText('arXiv ID or link'), 'invalid')
    await user.click(screen.getByRole('button', { name: /Run assessment/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('does not look like an arXiv ID')
  })

  it('names the host when a non-arXiv link is pasted', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await user.type(screen.getByLabelText('arXiv ID or link'), 'https://openreview.net/forum?id=x')
    await user.click(screen.getByRole('button', { name: /Run assessment/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('Only arxiv.org links are supported')
  })

  it('submits the canonical ID rather than the pasted URL', async () => {
    const user = userEvent.setup()
    const onSubmit = renderWorkspace()
    await user.type(screen.getByLabelText('arXiv ID or link'), 'https://arxiv.org/pdf/2307.08122v3.pdf')
    await user.click(screen.getByRole('button', { name: /Run assessment/ }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      source_type: 'arxiv',
      arxiv_id: '2307.08122v3',
    }))
  })

  it('submits the GPT model selected by the user', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const gpt = {
      ...options.models[0],
      model_id: 'model_gpt_v1',
      display_name: 'GPT Review Model',
      provider: 'openai',
      model_version: 'gpt-4.1-mini',
      is_default: false,
    }
    renderWorkspace(onSubmit, [...options.models, gpt])

    await user.click(screen.getByRole('radio', { name: /GPT Review Model/ }))
    await user.type(screen.getByLabelText('arXiv ID or link'), '2307.08122v3')
    await user.click(screen.getByRole('button', { name: /Run assessment/ }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ model_id: 'model_gpt_v1' }))
  })

  it('shows GPT as unavailable without letting it be selected', async () => {
    const gpt = {
      ...options.models[0],
      model_id: 'model_gpt_v1',
      display_name: 'GPT Review Model',
      provider: 'openai',
      model_version: 'gpt-4.1-mini',
      is_default: false,
      is_available: false,
    }
    renderWorkspace(vi.fn(), [...options.models, gpt])

    const choice = screen.getByRole('radio', { name: /GPT Review Model/ })
    expect(choice).toBeDisabled()
    expect(choice).toHaveTextContent('Unavailable')
    expect(screen.getByRole('radio', { name: /Standard analysis/ })).toHaveAttribute('aria-checked', 'true')
  })
})
