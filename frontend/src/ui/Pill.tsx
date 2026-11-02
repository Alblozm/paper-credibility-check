import type { ReactNode } from 'react'
import type { CredibilityClaimItem, CredibilityReferenceItem } from '../types/credibility'
import { outcomeNames, referenceNames } from './names'

export function OutcomePill({ outcome }: { outcome: CredibilityClaimItem['outcome'] }) {
  return <span className={`cs-pill ${outcome}`}>{outcomeNames[outcome]}</span>
}

export function ReferencePill({ outcome }: { outcome: CredibilityReferenceItem['outcome'] }) {
  return <span className={`cs-pill ${outcome}`}>{referenceNames[outcome]}</span>
}

export function Badge({ children, gap = false, on = false }: { children: ReactNode; gap?: boolean; on?: boolean }) {
  return <span className={`cs-badge ${gap ? 'is-gap' : ''} ${on ? 'is-on' : ''}`}>{children}</span>
}
