# Generic Configurable Approval Workflow

Configurable multi-stage approval workflows for any Odoo model.

## Attaching the workflow to a model

Everything is configuration except one step: the target model must inherit the
mixin, so the approval fields and buttons exist on it.

```python
class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'approval.mixin']
```

Then add the buttons and the status to its form view:

```xml
<xpath expr="//header" position="inside">
    <button name="action_submit_for_approval" type="object" string="Submit for Approval"
            class="btn-primary" invisible="approval_state != 'draft'"/>
    <button name="action_approve" type="object" string="Approve"
            class="btn-primary" invisible="not can_approve"/>
    <button name="action_open_reject_wizard" type="object" string="Reject"
            invisible="not can_approve"/>
    <button name="action_open_send_back_wizard" type="object" string="Send Back"
            invisible="not can_approve"/>
    <field name="can_approve" invisible="1"/>
    <field name="approval_state" widget="statusbar"/>
</xpath>
<xpath expr="//div[hasclass('oe_button_box')]" position="inside">
    <button class="oe_stat_button" type="object" icon="fa-history"
            name="action_view_approval_history"
            invisible="not approval_history_count">
        <field name="approval_history_count" widget="statinfo" string="Approvals"/>
    </button>
</xpath>
```

`can_approve` is True only for the approvers of the current stage who have not
approved yet, so the buttons are hidden from everyone else.

## Configuration

**Approvals > Configuration > Approval Workflows**

* **Target Model** and **Trigger Criteria** (a domain). The first active workflow
  whose criteria match the record is applied, in sequence order.
  Example: `[('amount_total', '>', 10000)]`.
* **Approval Stages**: name, sequence, approvers, method, actions, next stage,
  behaviour on rejection and on send back, optional stage condition and email
  templates. Drag to reorder.

### Approvers
By specific users, employees, job position, or the employee's manager
(`Manager Level` 1 = direct manager, 2 = manager's manager).

### Approval method
One approver is enough / all approvers required / minimum number of approvals.

### Behaviour
On rejection: reject, return to draft, return to the previous stage, or return to
a specific stage. On send back: previous stage, draft, or a specific stage.

## Hooks

Override on the target model to run your own logic:

```python
def _on_approval_done(self):
    return self.button_confirm()

def _on_approval_rejected(self):
    return self.button_cancel()
```

## History

Every action is written to `approval.history`: stage, action, user, date,
previous status, new status and comment. Visible per record through the smart
button, and globally under **Approvals > Approval History**.
