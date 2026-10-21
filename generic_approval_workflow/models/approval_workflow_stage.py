# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval


class ApprovalWorkflowStage(models.Model):
    _name = 'approval.workflow.stage'
    _description = 'Approval Workflow Stage'
    _order = 'sequence, id'

    name = fields.Char(string='Stage Name', required=True, translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    line_type = fields.Selection(
        selection=[
            ('state', 'Model State'),
            ('approval', 'Approval Stage'),
        ],
        string='Line Type', required=True, default='approval',
        help='Model State lines mirror the states of the target model and carry no '
             'approval. Approval Stage lines are the approvals inserted between them.')
    model_state_id = fields.Many2one(
        'approval.model.state', string='Model State', ondelete='cascade',
        help='State of the target model this line stands for.')
    state_value = fields.Char(related='model_state_id.value', string='State Value', store=True)
    target_state_id = fields.Many2one(
        'approval.model.state', string='State after Approval',
        domain="[('model_name', '=', model_name)]",
        help='State written on the record once this approval is granted. '
             'Empty means the next Model State line below.')
    workflow_id = fields.Many2one(
        'approval.workflow', string='Workflow', required=True,
        ondelete='cascade', index=True)
    model_name = fields.Char(related='workflow_id.model_name', string='Model Name')
    active = fields.Boolean(string='Active', default=True)

    # --- approvers -------------------------------------------------------
    approver_type = fields.Selection(
        selection=[
            ('user', 'Specific Users'),
            ('employee', 'Specific Employees'),
            ('job', 'Job Position'),
            ('manager', "Employee's Manager"),
        ],
        string='Approver Type', required=True, default='user')
    user_ids = fields.Many2many(
        'res.users', 'approval_stage_user_rel', 'stage_id', 'user_id',
        string='Users')
    employee_ids = fields.Many2many(
        'hr.employee', 'approval_stage_employee_rel', 'stage_id', 'employee_id',
        string='Employees')
    job_id = fields.Many2one('hr.job', string='Job Position')
    manager_level = fields.Integer(
        string='Manager Level', default=1,
        help='1 = direct manager, 2 = manager of the manager, and so on.')

    approval_method = fields.Selection(
        selection=[
            ('one', 'One approver is enough'),
            ('all', 'All approvers are required'),
            ('minimum', 'Minimum number of approvals'),
        ],
        string='Approval Method', required=True, default='one')
    min_approvals = fields.Integer(
        string='Minimum Approvals', default=1,
        help='Number of approvals required when the method is "Minimum number of approvals".')

    # --- available actions ----------------------------------------------
    button_label = fields.Char(
        string='Button Label',
        help='Label of the generated button. Defaults to the stage name.')
    allow_approve = fields.Boolean(string='Allow Approve', default=True)
    allow_reject = fields.Boolean(string='Allow Reject', default=True)
    allow_send_back = fields.Boolean(string='Allow Send Back', default=True)
    allow_cancel = fields.Boolean(string='Allow Cancel', default=True)
    allow_reset_draft = fields.Boolean(string='Allow Reset to Draft', default=False)

    # --- flow ------------------------------------------------------------
    next_stage_id = fields.Many2one(
        'approval.workflow.stage', string='Next Stage after Approval',
        domain="[('workflow_id', '=', workflow_id), ('id', '!=', id)]",
        help='Leave empty to move to the next stage by sequence; '
             'when there is none the record is fully approved.')
    reject_behavior = fields.Selection(
        selection=[
            ('reject', 'Reject the request'),
            ('draft', 'Return to Draft'),
            ('previous', 'Return to the previous stage'),
            ('stage', 'Return to a specific stage'),
        ],
        string='Behaviour on Rejection', required=True, default='reject')
    reject_stage_id = fields.Many2one(
        'approval.workflow.stage', string='Rejection Stage',
        domain="[('workflow_id', '=', workflow_id), ('id', '!=', id)]")
    reject_model_state_id = fields.Many2one(
        'approval.model.state', string='State on Rejection',
        domain="[('model_name', '=', model_name)]",
        help='State written on the record when the request is rejected. '
             'Left empty, a state named rejected or cancelled is used.')
    return_behavior = fields.Selection(
        selection=[
            ('previous', 'Return to the previous stage'),
            ('draft', 'Return to Draft'),
            ('stage', 'Return to a specific stage'),
        ],
        string='Behaviour on Send Back', required=True, default='previous')
    return_stage_id = fields.Many2one(
        'approval.workflow.stage', string='Send Back Stage',
        domain="[('workflow_id', '=', workflow_id), ('id', '!=', id)]")
    state_ids = fields.Many2many(
        'approval.model.state', 'approval_stage_model_state_rel', 'stage_id', 'state_id',
        string='Applicable States',
        domain="[('model_name', '=', model_name)]",
        help='States the record must be in for this stage to apply. '
             'Leave empty to apply whatever the state is.')
    condition = fields.Char(
        string='Stage Condition', default='[]',
        help='Optional domain. When it does not match the record, the stage is skipped.')

    # --- notifications ---------------------------------------------------
    mail_template_id = fields.Many2one(
        'mail.template', string='Stage Email Template',
        domain="[('model', '=', model_name)]",
        help='Sent to the approvers when the record reaches this stage.')
    approve_template_id = fields.Many2one(
        'mail.template', string='Approval Email Template',
        domain="[('model', '=', model_name)]")
    reject_template_id = fields.Many2one(
        'mail.template', string='Rejection Email Template',
        domain="[('model', '=', model_name)]")
    return_template_id = fields.Many2one(
        'mail.template', string='Send Back Email Template',
        domain="[('model', '=', model_name)]")
    notify_followers = fields.Boolean(
        string='Notify Followers', default=False,
        help='Also notify the followers of the record, not only the approvers.')
    extra_recipient_ids = fields.Many2many(
        'res.partner', 'approval_stage_partner_rel', 'stage_id', 'partner_id',
        string='Extra Recipients')

    @api.constrains('approval_method', 'min_approvals')
    def _check_min_approvals(self):
        for stage in self:
            if stage.approval_method == 'minimum' and stage.min_approvals < 1:
                raise ValidationError(
                    _('The minimum number of approvals of stage "%s" must be at least 1.',
                      stage.name))

    @api.constrains('condition', 'model_name')
    def _check_condition(self):
        for stage in self:
            if not stage.condition or not stage.model_name:
                continue
            try:
                domain = safe_eval(stage.condition)
                self.env[stage.model_name].search_count(domain)
            except Exception as error:
                raise ValidationError(
                    _('The condition of stage "%(name)s" is not a valid domain: %(error)s',
                      name=stage.name, error=error))

    def _get_trigger_state(self):
        """Model state the record must be in for this approval stage."""
        self.ensure_one()
        previous = self.workflow_id.stage_ids.filtered(
            lambda l: l.line_type == 'state' and (l.sequence, l.id) < (self.sequence, self.id))
        previous = previous.sorted(lambda l: (l.sequence, l.id))
        return previous[-1:].model_state_id if previous else self.env['approval.model.state']

    def _get_state_field(self):
        """حقل شريط الحالة الخاص بالمسار (حقل يدوي حتى يمكن إضافة قيم له)."""
        self.ensure_one()
        return self.env['ir.model.fields'].sudo().search([
            ('model', '=', self.workflow_id.model_name),
            ('name', '=', 'x_workflow_state'),
            ('ttype', '=', 'selection'),
        ], limit=1)

    def _get_stage_state_value(self):
        """القيمة التي تمثل هذه المرحلة في شريط الحالة."""
        self.ensure_one()
        return 'x_stage_%s' % self.id

    def _ensure_stage_state(self):
        """إضافة حالة في النموذج الهدف تمثل مرحلة الموافقة."""
        Selection = self.env['ir.model.fields.selection'].sudo()
        for stage in self.filtered(lambda l: l.line_type == 'approval'):
            field = stage._get_state_field()
            if not field:
                continue
            value = stage._get_stage_state_value()
            existing = Selection.search([
                ('field_id', '=', field.id), ('value', '=', value)], limit=1)
            values = {'name': stage.name or value, 'sequence': stage.sequence}
            if existing:
                existing.write(values)
            else:
                Selection.create(dict(values, field_id=field.id, value=value))

    def _remove_stage_state(self):
        Selection = self.env['ir.model.fields.selection'].sudo()
        for stage in self.filtered(lambda l: l.line_type == 'approval'):
            field = stage._get_state_field()
            if not field:
                continue
            Selection.search([
                ('field_id', '=', field.id),
                ('value', '=', stage._get_stage_state_value()),
            ]).unlink()

    @api.model_create_multi
    def create(self, vals_list):
        stages = super().create(vals_list)
        stages.mapped('workflow_id')._ensure_workflow_state_field()
        return stages

    def write(self, vals):
        res = super().write(vals)
        if {'name', 'sequence', 'line_type'} & set(vals):
            self.mapped('workflow_id')._ensure_workflow_state_field()
        return res

    def unlink(self):
        workflows = self.mapped('workflow_id')
        self._remove_stage_state()
        """Removing a Model State line hides that state in the backend."""
        for line in self.filtered(lambda l: l.line_type == 'state' and l.model_state_id):
            line.workflow_id.sudo().write({
                'excluded_state_ids': [(4, line.model_state_id.id)]})
        return super().unlink()

    def _get_reject_model_state(self):
        """حالة النموذج التي تكتب عند الرفض."""
        self.ensure_one()
        if self.reject_model_state_id:
            return self.reject_model_state_id
        states = self.env['approval.model.state'].sudo().search(
            [('model_name', '=', self.workflow_id.model_name)])
        for candidate in ('reject', 'rejected', 'refuse', 'refused'):
            match = states.filtered(lambda s: s.value == candidate)
            if match:
                return match[:1]
        for candidate in ('cancel', 'cancelled'):
            match = states.filtered(lambda s: s.value == candidate)
            if match:
                return match[:1]
        return self.env['approval.model.state']

    def _get_sibling_stages(self):
        """Approval stages sharing the same trigger state, in order."""
        self.ensure_one()
        trigger = self._get_trigger_state()
        siblings = self.workflow_id.stage_ids.filtered(
            lambda l: l.line_type == 'approval' and l._get_trigger_state() == trigger)
        return siblings.sorted(lambda l: (l.sequence, l.id))

    def _get_next_sibling(self):
        """Next approval stage on the same state, if any."""
        self.ensure_one()
        siblings = self._get_sibling_stages()
        following = siblings.filtered(lambda l: (l.sequence, l.id) > (self.sequence, self.id))
        return following[:1]

    def _is_last_of_state(self):
        """Whether this is the last approval before the record moves on."""
        self.ensure_one()
        return not self._get_next_sibling()

    def _get_target_state(self):
        """Model state written on the record once this stage is approved."""
        self.ensure_one()
        if self.target_state_id:
            return self.target_state_id
        following = self.workflow_id.stage_ids.filtered(
            lambda l: l.line_type == 'state' and (l.sequence, l.id) > (self.sequence, self.id))
        following = following.sorted(lambda l: (l.sequence, l.id))
        return following[:1].model_state_id if following else self.env['approval.model.state']

    def _match_record(self, record):
        """Whether this stage applies to the record."""
        self.ensure_one()
        if self.line_type == 'state':
            return False
        trigger = self._get_trigger_state()
        if trigger:
            field_name = trigger.field_name or 'state'
            if field_name in record._fields and record[field_name] != trigger.value:
                return False
        elif self.state_ids:
            field_name = self.state_ids[0].field_name or 'state'
            if field_name in record._fields:
                if record[field_name] not in self.state_ids.mapped('value'):
                    return False
        if not self.condition or self.condition.strip() in ('[]', ''):
            return True
        try:
            domain = safe_eval(self.condition)
        except Exception:
            return True
        return bool(record.filtered_domain(domain))

    # ------------------------------------------------------------------
    # Approvers
    # ------------------------------------------------------------------
    def _get_record_employee(self, record):
        """Employee the record is about, used to resolve the manager."""
        self.ensure_one()
        Employee = self.env['hr.employee']
        for field_name in ('employee_id', 'employee_ref_id', 'requested_by_employee_id'):
            if field_name in record._fields and record[field_name]:
                return record[field_name]
        user = record.create_uid or self.env.user
        return Employee.sudo().search([('user_id', '=', user.id)], limit=1)

    def _get_approver_users(self, record):
        """Users allowed to act on this stage for the given record."""
        self.ensure_one()
        users = self.env['res.users']
        if self.approver_type == 'user':
            users = self.user_ids
        elif self.approver_type == 'employee':
            users = self.employee_ids.sudo().mapped('user_id')
        elif self.approver_type == 'job':
            employees = self.env['hr.employee'].sudo().search([('job_id', '=', self.job_id.id)])
            users = employees.mapped('user_id')
        elif self.approver_type == 'manager':
            employee = self._get_record_employee(record).sudo()
            for _level in range(max(self.manager_level, 1)):
                employee = employee.parent_id
                if not employee:
                    break
            users = employee.user_id if employee else self.env['res.users']
        return users.filtered(lambda u: u.active)

    def _get_required_approvals(self, record):
        """How many distinct approvals this stage needs."""
        self.ensure_one()
        approvers = self._get_approver_users(record)
        if self.approval_method == 'all':
            return max(len(approvers), 1)
        if self.approval_method == 'minimum':
            return max(min(self.min_approvals, len(approvers) or self.min_approvals), 1)
        return 1

    def _get_notification_partners(self, record):
        """Partners to notify when the record reaches this stage."""
        self.ensure_one()
        partners = self._get_approver_users(record).mapped('partner_id')
        partners |= self.extra_recipient_ids
        if self.notify_followers and 'message_partner_ids' in record._fields:
            partners |= record.message_partner_ids
        return partners
