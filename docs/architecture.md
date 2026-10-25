# Architecture and authority

The foundation deliberately separates business systems from the durable control layer. Each HTTP subprocess receives only its own database credential and service token. The controller receives its own database credential and scoped service credentials, not direct receiver database credentials. The test harness is the trusted installer and corruption injector, never a production administrator design.

## Source authority

| Domain | Owner | Allowed local operation | Not inferred |
|---|---|---|---|
| Account, opportunity, owner, commitment | Salesforce-contract | Read versioned commercial events/current source/commitment | Invoice, payment or recognized revenue |
| Subscription/item/invoice/line | Stripe-contract | Initial activation and quarterly invoice from verified commitment | Native Stripe behavior outside this finite contract |
| Payment | Independent synthetic settlement fixture in billing database | Allocate available, persisted payment to invoice | Closed Won implies cash |
| Posting/payment application | NetSuite-contract | Invoice A/R debit, deferred-revenue credit, payment application | GAAP/IFRS revenue recognition |
| Identity, durable intent, verified status | PostgreSQL controller | Approved mapping resolution, execution, receipt verification | Controller status replaces independent source facts |

`Commercial` rejects unknown fields, bool/float money, unsupported currency and unversioned events. Content is verified against the CRM service, the current Opportunity, and its executed commitment. The current owner remains part of the exact source contract. Revisions do not silently overwrite accepted source history. Initial activation does not pretend to support amendments: a revised contract requires a separate explicit correction operation.

## Schema and grain

| Database | Tables / grain |
|---|---|
| CRM | Accounts, commitments, ingested source event identity and current Opportunity projection |
| Control | Account `(tenant,entity,canonical_account)`; mappings by source identity and provenance; inbox `(tenant,event_id)` with unique Opportunity revision; source head; outbox one stable effect per contract/revision/destination; attempts; exceptions; append-only audit |
| Billing | Registered customers; typed subscription, item, invoice, invoice line, settlement and allocation records; effects and exact receipts; controlled fault plans; request journal |
| ERP | Registered customer/legal-entity identities; invoice postings and payment applications; effects and independent receipts; request journal |

Versioned migrations live in [the source package](../src/integrity/migrations). Fresh installation is supported; upgrading existing schemas is not yet claimed. Receiver records and effects commit in the same PostgreSQL transaction. Primary keys prevent duplicate effect identifiers. The same identifier with different content returns 409. SQL triggers make accepted receipt/record bytes and controller audit/intent contents immutable; normal app identities have no DELETE grants.

Invoice lines, subscriptions and payment applications are separate records. No many-to-many monetary join is used. The first two accounts have monthly amounts of 1,000,000 and 400,000 integer cents; quarterly invoices are 3,000,000 and 1,200,000 cents. Independent settlement records provide 1,200,000 and 450,000 cents. All are synthetic USD, with no conversion or tax engine.

Business-effective interval, source event timestamp, inbox observation timestamp, receiver commit timestamp, invoice date, payment date and ERP posting date have distinct fields. The finite fixture uses first-day annual commitments and quarterly upfront invoicing. Posting date follows invoice date in this initial lane; later reconciliation must make posting-lag policy explicit.

## Design references

The inspected public reference revisions were Control Plane `be10215528be9c66b3820f4b715d364acaf59ecb`, Customer Signal & Activation `948d003185899e7cb9d34384789a93db07da39cd`, Revenue Metrics `b68230bc0756d80585fecf9bd4eb527b1ed7adc6`, Migration Assurance `4580619b41fbe1821ffbe09d6f33a7623a52d122`, and GTM Orchestration `6ba9e1cfcd4c6cff1ef6f42439b158d191b4fddb`. Their receipt, identity, authority, semantic-parity and financial-grain concepts informed this small implementation. Their code/evidence was not copied wholesale; their vendor acceptance is not acceptance for this repository. All remained read-only.

Contract shapes were checked against official [Salesforce sObject retrieval](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-retrieve-get.html), [Stripe idempotency](https://docs.stripe.com/api/idempotent_requests), [Stripe versioning](https://docs.stripe.com/api/versioning), and [NetSuite customer payment/application](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/article_0224093711.html) documentation. The local `/effects`, `/receipts` and typed record endpoints are explicit **project contracts**, not vendor endpoint emulations. Fault injection is a local test facility; it says nothing about live vendor quotas, retention, API field coverage or payment behavior.
