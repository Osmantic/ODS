import CustomIntegrations from '../components/CustomIntegrations'
import ServiceMap from './ServiceMap'
import '../settings-workspace.css'
import './integrations.css'

// One place for "is everything I depend on answering?": the systems the owner
// connected (hosted APIs, data engines, custom services) and the services ODS
// runs itself. `embedded` drops the page heading inside Settings, whose
// navigation already names the section.
export default function Integrations({ embedded = false }) {
  return (
    <div className={`integrations-page ${embedded ? 'is-embedded' : ''}`}>
      {!embedded && <header className="integrations-page-heading">
        <h1>Integrations</h1>
        <p>Whether the systems you connected and the services ODS runs are answering, and when ODS last checked.</p>
      </header>}
      <CustomIntegrations />
      <section className="integrations-section integrations-services" aria-label="ODS services">
        <ServiceMap compact title="ODS services" />
      </section>
    </div>
  )
}
