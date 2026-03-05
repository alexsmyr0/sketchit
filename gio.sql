/*
 AeroNetB Aerospace Supply Chain Management
 Relational Model (SQL Server T-SQL)
 Includes:
 - Core master data (suppliers, parts, users/roles)
 - Orders, shipments, QC and certifications
 - IoT/equipment monitoring and alerts
 - RBAC support, audit logging, versioning/immutability controls
 - Dummy DML for demo/testing
*/

SET NOCOUNT ON;

/* =========================
   CLEANUP (safe rerun order)
========================= */
IF OBJECT_ID('dbo.trg_certification_prevent_mutation', 'TR') IS NOT NULL DROP TRIGGER dbo.trg_certification_prevent_mutation;

IF OBJECT_ID('dbo.audit_log', 'U') IS NOT NULL DROP TABLE dbo.audit_log;
IF OBJECT_ID('dbo.maintenance_alert', 'U') IS NOT NULL DROP TABLE dbo.maintenance_alert;
IF OBJECT_ID('dbo.sensor_reading', 'U') IS NOT NULL DROP TABLE dbo.sensor_reading;
IF OBJECT_ID('dbo.iot_device', 'U') IS NOT NULL DROP TABLE dbo.iot_device;
IF OBJECT_ID('dbo.equipment_asset', 'U') IS NOT NULL DROP TABLE dbo.equipment_asset;
IF OBJECT_ID('dbo.facility', 'U') IS NOT NULL DROP TABLE dbo.facility;

IF OBJECT_ID('dbo.certification_signature', 'U') IS NOT NULL DROP TABLE dbo.certification_signature;
IF OBJECT_ID('dbo.certification_material_trace', 'U') IS NOT NULL DROP TABLE dbo.certification_material_trace;
IF OBJECT_ID('dbo.component_certification', 'U') IS NOT NULL DROP TABLE dbo.component_certification;

IF OBJECT_ID('dbo.qc_report_version', 'U') IS NOT NULL DROP TABLE dbo.qc_report_version;
IF OBJECT_ID('dbo.qc_report', 'U') IS NOT NULL DROP TABLE dbo.qc_report;

IF OBJECT_ID('dbo.shipment_checkpoint', 'U') IS NOT NULL DROP TABLE dbo.shipment_checkpoint;
IF OBJECT_ID('dbo.shipment_condition', 'U') IS NOT NULL DROP TABLE dbo.shipment_condition;
IF OBJECT_ID('dbo.shipment', 'U') IS NOT NULL DROP TABLE dbo.shipment;
IF OBJECT_ID('dbo.purchase_order', 'U') IS NOT NULL DROP TABLE dbo.purchase_order;

IF OBJECT_ID('dbo.part_media', 'U') IS NOT NULL DROP TABLE dbo.part_media;
IF OBJECT_ID('dbo.part_baseline_spec', 'U') IS NOT NULL DROP TABLE dbo.part_baseline_spec;
IF OBJECT_ID('dbo.part_supplier_variant', 'U') IS NOT NULL DROP TABLE dbo.part_supplier_variant;
IF OBJECT_ID('dbo.part', 'U') IS NOT NULL DROP TABLE dbo.part;

IF OBJECT_ID('dbo.supplier_accreditation', 'U') IS NOT NULL DROP TABLE dbo.supplier_accreditation;
IF OBJECT_ID('dbo.accreditation_type', 'U') IS NOT NULL DROP TABLE dbo.accreditation_type;
IF OBJECT_ID('dbo.supplier', 'U') IS NOT NULL DROP TABLE dbo.supplier;

IF OBJECT_ID('dbo.equipment_engineer_profile', 'U') IS NOT NULL DROP TABLE dbo.equipment_engineer_profile;
IF OBJECT_ID('dbo.auditor_profile', 'U') IS NOT NULL DROP TABLE dbo.auditor_profile;
IF OBJECT_ID('dbo.supply_chain_manager_profile', 'U') IS NOT NULL DROP TABLE dbo.supply_chain_manager_profile;
IF OBJECT_ID('dbo.quality_inspector_profile', 'U') IS NOT NULL DROP TABLE dbo.quality_inspector_profile;
IF OBJECT_ID('dbo.procurement_officer_profile', 'U') IS NOT NULL DROP TABLE dbo.procurement_officer_profile;

IF OBJECT_ID('dbo.app_user', 'U') IS NOT NULL DROP TABLE dbo.app_user;
IF OBJECT_ID('dbo.department', 'U') IS NOT NULL DROP TABLE dbo.department;

/* =========================
   ACCESS / USER DOMAIN
========================= */
CREATE TABLE dbo.department (
	department_id INT IDENTITY(1,1) PRIMARY KEY,
	department_name NVARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE dbo.app_user (
	emp_id NVARCHAR(20) PRIMARY KEY,
	full_name NVARCHAR(150) NOT NULL,
	job_title NVARCHAR(100) NOT NULL,
	role_name NVARCHAR(50) NOT NULL,
	department_id INT NOT NULL,
	email NVARCHAR(150) NOT NULL UNIQUE,
	phone NVARCHAR(50) NULL,
	access_level NVARCHAR(20) NOT NULL,
	auth_id NVARCHAR(100) NOT NULL UNIQUE,
	is_active BIT NOT NULL DEFAULT 1,
	created_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	CONSTRAINT FK_app_user_department FOREIGN KEY (department_id) REFERENCES dbo.department(department_id),
	CONSTRAINT CK_app_user_role CHECK (role_name IN (
		'PROCUREMENT_OFFICER', 'QUALITY_INSPECTOR', 'SUPPLY_CHAIN_MANAGER', 'EQUIPMENT_ENGINEER', 'AUDITOR'
	)),
	CONSTRAINT CK_app_user_access CHECK (access_level IN ('READ', 'WRITE', 'APPROVE', 'AUDIT', 'ADMIN'))
);

CREATE TABLE dbo.procurement_officer_profile (
	emp_id NVARCHAR(20) PRIMARY KEY,
	region_managed NVARCHAR(80) NULL,
	supplier_portfolio NVARCHAR(250) NULL,
	authorization_limit DECIMAL(18,2) NOT NULL,
	CONSTRAINT FK_procurement_profile_user FOREIGN KEY (emp_id) REFERENCES dbo.app_user(emp_id)
);

CREATE TABLE dbo.quality_inspector_profile (
	emp_id NVARCHAR(20) PRIMARY KEY,
	inspector_cert_ids NVARCHAR(250) NULL,
	specialization NVARCHAR(120) NULL,
	digital_signature_ref NVARCHAR(500) NULL,
	CONSTRAINT FK_quality_profile_user FOREIGN KEY (emp_id) REFERENCES dbo.app_user(emp_id)
);

CREATE TABLE dbo.supply_chain_manager_profile (
	emp_id NVARCHAR(20) PRIMARY KEY,
	assigned_product_lines NVARCHAR(250) NULL,
	reporting_level NVARCHAR(50) NULL,
	kpi_preferences_json NVARCHAR(MAX) NULL,
	CONSTRAINT FK_supply_chain_manager_profile_user FOREIGN KEY (emp_id) REFERENCES dbo.app_user(emp_id)
);

CREATE TABLE dbo.equipment_engineer_profile (
	emp_id NVARCHAR(20) PRIMARY KEY,
	engineering_license NVARCHAR(100) NULL,
	equipment_specialization NVARCHAR(150) NULL,
	assigned_facility NVARCHAR(120) NULL,
	machine_groups_json NVARCHAR(MAX) NULL,
	CONSTRAINT FK_equipment_engineer_profile_user FOREIGN KEY (emp_id) REFERENCES dbo.app_user(emp_id)
);

CREATE TABLE dbo.auditor_profile (
	emp_id NVARCHAR(20) PRIMARY KEY,
	authority_name NVARCHAR(150) NULL,
	accreditation_license_id NVARCHAR(100) NULL,
	audit_scope NVARCHAR(120) NULL,
	read_only_mode BIT NOT NULL DEFAULT 1,
	CONSTRAINT FK_auditor_profile_user FOREIGN KEY (emp_id) REFERENCES dbo.app_user(emp_id)
);

/* =========================
   SUPPLIER / PART DOMAIN
========================= */
CREATE TABLE dbo.supplier (
	supplier_id NVARCHAR(30) PRIMARY KEY,
	business_name NVARCHAR(200) NOT NULL,
	address_line NVARCHAR(250) NULL,
	country NVARCHAR(80) NULL,
	contact_email NVARCHAR(150) NULL,
	contact_phone NVARCHAR(50) NULL,
	accreditation_status NVARCHAR(30) NOT NULL,
	created_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	CONSTRAINT CK_supplier_accreditation_status CHECK (accreditation_status IN ('ACTIVE', 'PENDING', 'SUSPENDED', 'EXPIRED'))
);

CREATE TABLE dbo.accreditation_type (
	accreditation_code NVARCHAR(30) PRIMARY KEY,
	accreditation_name NVARCHAR(120) NOT NULL,
	issuing_authority NVARCHAR(120) NULL
);

CREATE TABLE dbo.supplier_accreditation (
	supplier_id NVARCHAR(30) NOT NULL,
	accreditation_code NVARCHAR(30) NOT NULL,
	certificate_no NVARCHAR(100) NULL,
	issue_date DATE NULL,
	expiry_date DATE NULL,
	PRIMARY KEY (supplier_id, accreditation_code),
	CONSTRAINT FK_supplier_accreditation_supplier FOREIGN KEY (supplier_id) REFERENCES dbo.supplier(supplier_id),
	CONSTRAINT FK_supplier_accreditation_type FOREIGN KEY (accreditation_code) REFERENCES dbo.accreditation_type(accreditation_code)
);

CREATE TABLE dbo.part (
	part_id NVARCHAR(40) PRIMARY KEY,
	part_name NVARCHAR(200) NOT NULL,
	part_description NVARCHAR(MAX) NULL,
	part_category NVARCHAR(80) NULL,
	baseline_standard_ref NVARCHAR(120) NULL,
	criticality NVARCHAR(20) NOT NULL DEFAULT 'MEDIUM',
	CONSTRAINT CK_part_criticality CHECK (criticality IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'))
);

CREATE TABLE dbo.part_baseline_spec (
	part_id NVARCHAR(40) PRIMARY KEY,
	tensile_strength_mpa DECIMAL(10,2) NULL,
	fatigue_limit_mpa DECIMAL(10,2) NULL,
	yield_point_mpa DECIMAL(10,2) NULL,
	process_details NVARCHAR(MAX) NULL,
	tolerance_spec NVARCHAR(250) NULL,
	geometry_file_ref NVARCHAR(500) NULL,
	engineering_notes NVARCHAR(MAX) NULL,
	CONSTRAINT FK_part_baseline_spec_part FOREIGN KEY (part_id) REFERENCES dbo.part(part_id)
);

CREATE TABLE dbo.part_media (
	media_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	part_id NVARCHAR(40) NOT NULL,
	media_type NVARCHAR(30) NOT NULL,
	file_ref NVARCHAR(500) NOT NULL,
	description NVARCHAR(250) NULL,
	uploaded_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	CONSTRAINT FK_part_media_part FOREIGN KEY (part_id) REFERENCES dbo.part(part_id),
	CONSTRAINT CK_part_media_type CHECK (media_type IN ('CAD', 'DRAWING', 'IMAGE', 'PDF', 'OTHER'))
);

CREATE TABLE dbo.part_supplier_variant (
	part_supplier_variant_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	part_id NVARCHAR(40) NOT NULL,
	supplier_id NVARCHAR(30) NOT NULL,
	customization_summary NVARCHAR(MAX) NULL,
	process_variant_details NVARCHAR(MAX) NULL,
	handling_requirements NVARCHAR(MAX) NULL,
	variant_notes NVARCHAR(MAX) NULL,
	unit_price DECIMAL(18,2) NULL,
	lead_time_days INT NULL,
	is_preferred BIT NOT NULL DEFAULT 0,
	created_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	CONSTRAINT FK_part_supplier_variant_part FOREIGN KEY (part_id) REFERENCES dbo.part(part_id),
	CONSTRAINT FK_part_supplier_variant_supplier FOREIGN KEY (supplier_id) REFERENCES dbo.supplier(supplier_id),
	CONSTRAINT UQ_part_supplier UNIQUE (part_id, supplier_id)
);

/* =========================
   ORDER / SHIPMENT DOMAIN
========================= */
CREATE TABLE dbo.purchase_order (
	order_id NVARCHAR(40) PRIMARY KEY,
	supplier_id NVARCHAR(30) NOT NULL,
	part_id NVARCHAR(40) NOT NULL,
	created_by_emp_id NVARCHAR(20) NOT NULL,
	order_date DATE NOT NULL,
	desired_delivery_date DATE NOT NULL,
	actual_delivery_date DATE NULL,
	quantity INT NOT NULL,
	total_amount DECIMAL(18,2) NULL,
	currency_code NCHAR(3) NULL,
	status NVARCHAR(20) NOT NULL,
	CONSTRAINT FK_purchase_order_supplier FOREIGN KEY (supplier_id) REFERENCES dbo.supplier(supplier_id),
	CONSTRAINT FK_purchase_order_part FOREIGN KEY (part_id) REFERENCES dbo.part(part_id),
	CONSTRAINT FK_purchase_order_creator FOREIGN KEY (created_by_emp_id) REFERENCES dbo.app_user(emp_id),
	CONSTRAINT CK_purchase_order_status CHECK (status IN ('PLACED', 'CONFIRMED', 'DISPATCHED', 'DELIVERED', 'COMPLETED', 'CANCELLED')),
	CONSTRAINT CK_purchase_order_qty CHECK (quantity > 0)
);

CREATE TABLE dbo.shipment (
	shipment_id NVARCHAR(40) PRIMARY KEY,
	order_id NVARCHAR(40) NOT NULL,
	tracking_no NVARCHAR(100) NOT NULL,
	carrier_name NVARCHAR(120) NULL,
	port_of_entry NVARCHAR(150) NULL,
	shipped_at DATETIME2(0) NULL,
	estimated_arrival DATETIME2(0) NULL,
	actual_arrival DATETIME2(0) NULL,
	shipment_status NVARCHAR(20) NOT NULL,
	CONSTRAINT FK_shipment_order FOREIGN KEY (order_id) REFERENCES dbo.purchase_order(order_id),
	CONSTRAINT UQ_shipment_tracking UNIQUE (tracking_no),
	CONSTRAINT CK_shipment_status CHECK (shipment_status IN ('PREPARING', 'IN_TRANSIT', 'ARRIVED', 'CUSTOMS_HOLD', 'DELIVERED'))
);

CREATE TABLE dbo.shipment_checkpoint (
	checkpoint_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	shipment_id NVARCHAR(40) NOT NULL,
	checkpoint_time DATETIME2(0) NOT NULL,
	latitude DECIMAL(10,6) NULL,
	longitude DECIMAL(10,6) NULL,
	location_text NVARCHAR(200) NULL,
	status_note NVARCHAR(250) NULL,
	CONSTRAINT FK_shipment_checkpoint_shipment FOREIGN KEY (shipment_id) REFERENCES dbo.shipment(shipment_id)
);

CREATE TABLE dbo.shipment_condition (
	condition_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	shipment_id NVARCHAR(40) NOT NULL,
	observed_at DATETIME2(0) NOT NULL,
	temperature_c DECIMAL(8,3) NULL,
	vibration_ms2 DECIMAL(10,4) NULL,
	pressure_kpa DECIMAL(10,3) NULL,
	shock_detected BIT NULL,
	condition_note NVARCHAR(250) NULL,
	CONSTRAINT FK_shipment_condition_shipment FOREIGN KEY (shipment_id) REFERENCES dbo.shipment(shipment_id)
);

/* =========================
   QUALITY / CERTIFICATION DOMAIN
========================= */
CREATE TABLE dbo.qc_report (
	report_id NVARCHAR(40) PRIMARY KEY,
	order_id NVARCHAR(40) NOT NULL,
	part_id NVARCHAR(40) NOT NULL,
	supplier_id NVARCHAR(30) NOT NULL,
	report_type NVARCHAR(40) NOT NULL,
	current_version_no INT NOT NULL DEFAULT 1,
	current_status NVARCHAR(20) NOT NULL,
	created_by_emp_id NVARCHAR(20) NOT NULL,
	approved_by_emp_id NVARCHAR(20) NULL,
	created_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	approved_at DATETIME2(0) NULL,
	CONSTRAINT FK_qc_report_order FOREIGN KEY (order_id) REFERENCES dbo.purchase_order(order_id),
	CONSTRAINT FK_qc_report_part FOREIGN KEY (part_id) REFERENCES dbo.part(part_id),
	CONSTRAINT FK_qc_report_supplier FOREIGN KEY (supplier_id) REFERENCES dbo.supplier(supplier_id),
	CONSTRAINT FK_qc_report_creator FOREIGN KEY (created_by_emp_id) REFERENCES dbo.app_user(emp_id),
	CONSTRAINT FK_qc_report_approver FOREIGN KEY (approved_by_emp_id) REFERENCES dbo.app_user(emp_id),
	CONSTRAINT CK_qc_report_type CHECK (report_type IN ('VISUAL', 'DIMENSIONAL', 'NDT', 'ENVIRONMENTAL', 'COMBINED')),
	CONSTRAINT CK_qc_report_status CHECK (current_status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED'))
);

CREATE TABLE dbo.qc_report_version (
	report_version_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	report_id NVARCHAR(40) NOT NULL,
	version_no INT NOT NULL,
	inspector_emp_id NVARCHAR(20) NOT NULL,
	result NVARCHAR(10) NOT NULL,
	findings_json NVARCHAR(MAX) NULL,
	failure_cause NVARCHAR(250) NULL,
	signature_ref NVARCHAR(500) NULL,
	recorded_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	is_approved_snapshot BIT NOT NULL DEFAULT 0,
	CONSTRAINT FK_qc_report_version_report FOREIGN KEY (report_id) REFERENCES dbo.qc_report(report_id),
	CONSTRAINT FK_qc_report_version_inspector FOREIGN KEY (inspector_emp_id) REFERENCES dbo.app_user(emp_id),
	CONSTRAINT UQ_qc_report_version UNIQUE (report_id, version_no),
	CONSTRAINT CK_qc_result CHECK (result IN ('PASS', 'FAIL'))
);

CREATE TABLE dbo.component_certification (
	certification_id NVARCHAR(40) PRIMARY KEY,
	report_id NVARCHAR(40) NOT NULL,
	part_id NVARCHAR(40) NOT NULL,
	supplier_id NVARCHAR(30) NOT NULL,
	inspector_emp_id NVARCHAR(20) NOT NULL,
	certification_status NVARCHAR(20) NOT NULL,
	cert_payload_json NVARCHAR(MAX) NULL,
	issued_at DATETIME2(0) NULL,
	approved_finalized_at DATETIME2(0) NULL,
	immutable_flag AS (CASE WHEN approved_finalized_at IS NULL THEN 0 ELSE 1 END) PERSISTED,
	CONSTRAINT FK_component_certification_report FOREIGN KEY (report_id) REFERENCES dbo.qc_report(report_id),
	CONSTRAINT FK_component_certification_part FOREIGN KEY (part_id) REFERENCES dbo.part(part_id),
	CONSTRAINT FK_component_certification_supplier FOREIGN KEY (supplier_id) REFERENCES dbo.supplier(supplier_id),
	CONSTRAINT FK_component_certification_inspector FOREIGN KEY (inspector_emp_id) REFERENCES dbo.app_user(emp_id),
	CONSTRAINT CK_component_certification_status CHECK (certification_status IN ('DRAFT', 'UNDER_REVIEW', 'APPROVED', 'REVOKED'))
);

CREATE TABLE dbo.certification_material_trace (
	trace_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	certification_id NVARCHAR(40) NOT NULL,
	raw_material_batch_no NVARCHAR(80) NOT NULL,
	origin_country NVARCHAR(80) NULL,
	mill_certificate_ref NVARCHAR(250) NULL,
	trace_notes NVARCHAR(250) NULL,
	CONSTRAINT FK_certification_material_trace_cert FOREIGN KEY (certification_id) REFERENCES dbo.component_certification(certification_id)
);

CREATE TABLE dbo.certification_signature (
	signature_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	certification_id NVARCHAR(40) NOT NULL,
	signer_emp_id NVARCHAR(20) NOT NULL,
	signature_hash NVARCHAR(500) NOT NULL,
	stamp_ref NVARCHAR(500) NULL,
	signed_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	CONSTRAINT FK_certification_signature_cert FOREIGN KEY (certification_id) REFERENCES dbo.component_certification(certification_id),
	CONSTRAINT FK_certification_signature_user FOREIGN KEY (signer_emp_id) REFERENCES dbo.app_user(emp_id)
);

/* Prevent update/delete of immutable approved certifications */
GO
CREATE TRIGGER dbo.trg_certification_prevent_mutation
ON dbo.component_certification
INSTEAD OF UPDATE, DELETE
AS
BEGIN
	SET NOCOUNT ON;

	IF EXISTS (
		SELECT 1
		FROM deleted d
		WHERE d.approved_finalized_at IS NOT NULL
		   OR d.certification_status = 'APPROVED'
	)
	BEGIN
		THROW 50001, 'Approved/finalized certifications are immutable and cannot be updated or deleted.', 1;
		RETURN;
	END;

	IF EXISTS (SELECT 1 FROM inserted)
	BEGIN
		UPDATE c
		SET
			report_id = i.report_id,
			part_id = i.part_id,
			supplier_id = i.supplier_id,
			inspector_emp_id = i.inspector_emp_id,
			certification_status = i.certification_status,
			cert_payload_json = i.cert_payload_json,
			issued_at = i.issued_at,
			approved_finalized_at = i.approved_finalized_at
		FROM dbo.component_certification c
		INNER JOIN inserted i
			ON c.certification_id = i.certification_id;
	END
	ELSE
	BEGIN
		DELETE c
		FROM dbo.component_certification c
		INNER JOIN deleted d
			ON c.certification_id = d.certification_id;
	END
END;
GO

/* =========================
   EQUIPMENT / IOT DOMAIN
========================= */
CREATE TABLE dbo.facility (
	facility_id NVARCHAR(30) PRIMARY KEY,
	facility_name NVARCHAR(150) NOT NULL,
	country NVARCHAR(80) NULL,
	city NVARCHAR(80) NULL
);

CREATE TABLE dbo.equipment_asset (
	equipment_id NVARCHAR(40) PRIMARY KEY,
	facility_id NVARCHAR(30) NOT NULL,
	equipment_name NVARCHAR(150) NOT NULL,
	equipment_type NVARCHAR(80) NULL,
	status NVARCHAR(20) NOT NULL,
	last_maintenance_at DATETIME2(0) NULL,
	next_maintenance_due DATETIME2(0) NULL,
	CONSTRAINT FK_equipment_asset_facility FOREIGN KEY (facility_id) REFERENCES dbo.facility(facility_id),
	CONSTRAINT CK_equipment_asset_status CHECK (status IN ('OK', 'WARNING', 'CRITICAL', 'OFFLINE'))
);

CREATE TABLE dbo.iot_device (
	device_id NVARCHAR(40) PRIMARY KEY,
	equipment_id NVARCHAR(40) NULL,
	shipment_id NVARCHAR(40) NULL,
	sensor_type NVARCHAR(30) NOT NULL,
	unit NVARCHAR(30) NULL,
	threshold_min DECIMAL(12,4) NULL,
	threshold_max DECIMAL(12,4) NULL,
	is_active BIT NOT NULL DEFAULT 1,
	CONSTRAINT FK_iot_device_equipment FOREIGN KEY (equipment_id) REFERENCES dbo.equipment_asset(equipment_id),
	CONSTRAINT FK_iot_device_shipment FOREIGN KEY (shipment_id) REFERENCES dbo.shipment(shipment_id),
	CONSTRAINT CK_iot_sensor_type CHECK (sensor_type IN ('TEMPERATURE', 'VIBRATION', 'PRESSURE', 'GPS', 'CYCLE_COUNT')),
	CONSTRAINT CK_iot_device_target CHECK (
		(equipment_id IS NOT NULL AND shipment_id IS NULL)
		OR (equipment_id IS NULL AND shipment_id IS NOT NULL)
	)
);

CREATE TABLE dbo.sensor_reading (
	reading_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	device_id NVARCHAR(40) NOT NULL,
	observed_at DATETIME2(3) NOT NULL,
	reading_value DECIMAL(14,5) NULL,
	latitude DECIMAL(10,6) NULL,
	longitude DECIMAL(10,6) NULL,
	raw_payload_json NVARCHAR(MAX) NULL,
	CONSTRAINT FK_sensor_reading_device FOREIGN KEY (device_id) REFERENCES dbo.iot_device(device_id)
);

CREATE TABLE dbo.maintenance_alert (
	alert_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	equipment_id NVARCHAR(40) NOT NULL,
	triggered_by_device_id NVARCHAR(40) NULL,
	alert_type NVARCHAR(40) NOT NULL,
	severity NVARCHAR(20) NOT NULL,
	message NVARCHAR(250) NOT NULL,
	triggered_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	resolved_at DATETIME2(0) NULL,
	resolved_by_emp_id NVARCHAR(20) NULL,
	CONSTRAINT FK_maintenance_alert_equipment FOREIGN KEY (equipment_id) REFERENCES dbo.equipment_asset(equipment_id),
	CONSTRAINT FK_maintenance_alert_device FOREIGN KEY (triggered_by_device_id) REFERENCES dbo.iot_device(device_id),
	CONSTRAINT FK_maintenance_alert_resolver FOREIGN KEY (resolved_by_emp_id) REFERENCES dbo.app_user(emp_id),
	CONSTRAINT CK_maintenance_alert_severity CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL'))
);

/* =========================
   AUDIT LOG DOMAIN
========================= */
CREATE TABLE dbo.audit_log (
	audit_id BIGINT IDENTITY(1,1) PRIMARY KEY,
	event_time DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
	emp_id NVARCHAR(20) NOT NULL,
	action_type NVARCHAR(30) NOT NULL,
	entity_name NVARCHAR(80) NOT NULL,
	entity_key NVARCHAR(120) NOT NULL,
	access_outcome NVARCHAR(20) NOT NULL,
	before_state_json NVARCHAR(MAX) NULL,
	after_state_json NVARCHAR(MAX) NULL,
	source_ip NVARCHAR(64) NULL,
	user_agent NVARCHAR(250) NULL,
	CONSTRAINT FK_audit_log_user FOREIGN KEY (emp_id) REFERENCES dbo.app_user(emp_id),
	CONSTRAINT CK_audit_log_action CHECK (action_type IN ('VIEW', 'INSERT', 'UPDATE', 'DELETE', 'APPROVE', 'EXPORT', 'LOGIN')),
	CONSTRAINT CK_audit_log_outcome CHECK (access_outcome IN ('SUCCESS', 'DENIED', 'ERROR'))
);

/* =========================
   PERFORMANCE INDEXES
========================= */
CREATE INDEX IX_purchase_order_supplier_status ON dbo.purchase_order(supplier_id, status, desired_delivery_date);
CREATE INDEX IX_shipment_order_status ON dbo.shipment(order_id, shipment_status, estimated_arrival);
CREATE INDEX IX_checkpoint_shipment_time ON dbo.shipment_checkpoint(shipment_id, checkpoint_time DESC);
CREATE INDEX IX_qc_report_supplier_status ON dbo.qc_report(supplier_id, current_status, created_at DESC);
CREATE INDEX IX_qc_version_report_time ON dbo.qc_report_version(report_id, version_no DESC, recorded_at DESC);
CREATE INDEX IX_sensor_reading_device_time ON dbo.sensor_reading(device_id, observed_at DESC);
CREATE INDEX IX_audit_emp_time ON dbo.audit_log(emp_id, event_time DESC);

/* =========================
   DUMMY DATA (DML)
========================= */
INSERT INTO dbo.department(department_name)
VALUES
('Procurement'),
('Quality'),
('Supply Chain'),
('Engineering'),
('Compliance');

INSERT INTO dbo.app_user(emp_id, full_name, job_title, role_name, department_id, email, phone, access_level, auth_id)
VALUES
('E1001', 'Nikos Pappas', 'Procurement Officer', 'PROCUREMENT_OFFICER', 1, 'nikos.pappas@aeronetb.local', '+30-210-1111111', 'WRITE', 'auth-np-1001'),
('E2001', 'Maria Ioannou', 'Quality Inspector', 'QUALITY_INSPECTOR', 2, 'maria.ioannou@aeronetb.local', '+30-210-2222222', 'APPROVE', 'auth-mi-2001'),
('E3001', 'George Antoniou', 'Supply Chain Manager', 'SUPPLY_CHAIN_MANAGER', 3, 'george.antoniou@aeronetb.local', '+30-210-3333333', 'READ', 'auth-ga-3001'),
('E4001', 'Elena Georgiadou', 'Equipment Engineer', 'EQUIPMENT_ENGINEER', 4, 'elena.georgiadou@aeronetb.local', '+30-210-4444444', 'WRITE', 'auth-eg-4001'),
('E5001', 'Sofia Markou', 'Auditor', 'AUDITOR', 5, 'sofia.markou@aeronetb.local', '+30-210-5555555', 'AUDIT', 'auth-sm-5001');

INSERT INTO dbo.procurement_officer_profile(emp_id, region_managed, supplier_portfolio, authorization_limit)
VALUES ('E1001', 'EU', 'MetalWorks, SkyComposites', 250000.00);

INSERT INTO dbo.quality_inspector_profile(emp_id, inspector_cert_ids, specialization, digital_signature_ref)
VALUES ('E2001', 'ISO17025-QI-7781;NDT-L2-5510', 'NDT, DIMENSIONAL', 'https://secure-sign.example/aeronetb/E2001.sig');

INSERT INTO dbo.supply_chain_manager_profile(emp_id, assigned_product_lines, reporting_level, kpi_preferences_json)
VALUES ('E3001', 'Fuselage;Wing Assemblies', 'GLOBAL', '{"kpis":["otd","defect_rate","eta_variance"]}');

INSERT INTO dbo.equipment_engineer_profile(emp_id, engineering_license, equipment_specialization, assigned_facility, machine_groups_json)
VALUES ('E4001', 'ENG-LIC-99872', 'CNC + Autoclave', 'ATH-PLANT-1', '{"groups":["CNC_LINE_A","AUTOCLAVE_B"]}');

INSERT INTO dbo.auditor_profile(emp_id, authority_name, accreditation_license_id, audit_scope, read_only_mode)
VALUES ('E5001', 'EASA Oversight Unit', 'AUD-EL-9921', 'EXTERNAL_COMPLIANCE', 1);

INSERT INTO dbo.accreditation_type(accreditation_code, accreditation_name, issuing_authority)
VALUES
('ISO9001', 'ISO 9001', 'ISO'),
('AS9100', 'AS9100', 'IAQG');

INSERT INTO dbo.supplier(supplier_id, business_name, address_line, country, contact_email, contact_phone, accreditation_status)
VALUES
('SUP-001', 'Aegean Aero Metals', '12 Industry Rd, Athens', 'Greece', 'sales@aegeanaero.example', '+30-210-1110000', 'ACTIVE'),
('SUP-002', 'Nordic Composite Tech', '45 Fjord Park, Oslo', 'Norway', 'support@nordiccomp.example', '+47-22-200000', 'ACTIVE'),
('SUP-003', 'Helios Precision Works', '88 Delta Ave, Turin', 'Italy', 'contact@heliospw.example', '+39-011-300000', 'ACTIVE');

INSERT INTO dbo.supplier_accreditation(supplier_id, accreditation_code, certificate_no, issue_date, expiry_date)
VALUES
('SUP-001', 'ISO9001', 'ISO9-001-AE', '2024-01-10', '2027-01-09'),
('SUP-001', 'AS9100', 'AS9-001-AE', '2024-02-01', '2027-01-31'),
('SUP-002', 'AS9100', 'AS9-002-NC', '2024-03-15', '2027-03-14');

INSERT INTO dbo.part(part_id, part_name, part_description, part_category, baseline_standard_ref, criticality)
VALUES
('PART-A320-FP-01', 'A320 Fuselage Panel', 'Primary fuselage outer panel section', 'FUSELAGE', 'STD-AERO-FUS-001', 'CRITICAL'),
('PART-A320-WR-02', 'A320 Wing Rib', 'Wing internal rib support', 'WING', 'STD-AERO-WING-007', 'HIGH');

INSERT INTO dbo.part_baseline_spec(part_id, tensile_strength_mpa, fatigue_limit_mpa, yield_point_mpa, process_details, tolerance_spec, geometry_file_ref, engineering_notes)
VALUES
('PART-A320-FP-01', 470.00, 240.00, 320.00, 'Heat treatment T6; CNC machining; anti-corrosion primer baseline', '±0.05mm flatness', 'cad://aircraft/a320/fuselage_panel_v3.step', 'Must satisfy baseline geometry and fatigue standards'),
('PART-A320-WR-02', 520.00, 260.00, 350.00, 'Composite layup + autoclave cure', '±0.03mm key edges', 'cad://aircraft/a320/wing_rib_v2.step', 'Environmental stress resistance mandatory');

INSERT INTO dbo.part_supplier_variant(part_id, supplier_id, customization_summary, process_variant_details, handling_requirements, variant_notes, unit_price, lead_time_days, is_preferred)
VALUES
('PART-A320-FP-01', 'SUP-001', 'Anti-corrosion coating + serialized RFID tags', 'Enhanced surface finishing cycle', 'Humidity-controlled storage', 'Optimized for lifecycle tracking', 18500.00, 18, 1),
('PART-A320-FP-01', 'SUP-002', 'Reinforced composite layering + shock-sensor packaging', 'Composite layering variant B', 'Shock-safe transit required', 'Higher fatigue resistance', 19200.00, 21, 0),
('PART-A320-FP-01', 'SUP-003', 'Lighter-weight heat-treatment optimization + digital twin data', 'Heat treatment profile H3', 'Avoid rapid thermal shifts', 'Lower material weight', 18850.00, 19, 0);

INSERT INTO dbo.part_media(part_id, media_type, file_ref, description)
VALUES
('PART-A320-FP-01', 'CAD', 'cad://aircraft/a320/fuselage_panel_v3.step', '3D model'),
('PART-A320-FP-01', 'DRAWING', 'docs://drawings/fp01_rev7.pdf', 'Engineering drawing rev7'),
('PART-A320-FP-01', 'IMAGE', 'img://prototype/fp01_proto.jpg', 'Prototype photo');

INSERT INTO dbo.purchase_order(order_id, supplier_id, part_id, created_by_emp_id, order_date, desired_delivery_date, actual_delivery_date, quantity, total_amount, currency_code, status)
VALUES
('PO-2026-0001', 'SUP-001', 'PART-A320-FP-01', 'E1001', '2026-02-10', '2026-03-20', NULL, 40, 740000.00, 'EUR', 'DISPATCHED'),
('PO-2026-0002', 'SUP-002', 'PART-A320-FP-01', 'E1001', '2026-02-14', '2026-03-28', NULL, 35, 672000.00, 'EUR', 'CONFIRMED');

INSERT INTO dbo.shipment(shipment_id, order_id, tracking_no, carrier_name, port_of_entry, shipped_at, estimated_arrival, actual_arrival, shipment_status)
VALUES
('SHIP-0001', 'PO-2026-0001', 'TRK-AX-445577', 'BlueSky Logistics', 'Piraeus Port', '2026-03-01 08:15:00', '2026-03-18 12:00:00', NULL, 'IN_TRANSIT');

INSERT INTO dbo.shipment_checkpoint(shipment_id, checkpoint_time, latitude, longitude, location_text, status_note)
VALUES
('SHIP-0001', '2026-03-01 20:00:00', 37.983810, 23.727539, 'Athens Hub', 'Departed origin warehouse'),
('SHIP-0001', '2026-03-03 09:30:00', 38.423733, 27.142826, 'Aegean Sea Corridor', 'On schedule');

INSERT INTO dbo.shipment_condition(shipment_id, observed_at, temperature_c, vibration_ms2, pressure_kpa, shock_detected, condition_note)
VALUES
('SHIP-0001', '2026-03-03 09:30:00', 18.400, 1.1200, 101.200, 0, 'Conditions normal');

INSERT INTO dbo.qc_report(report_id, order_id, part_id, supplier_id, report_type, current_version_no, current_status, created_by_emp_id)
VALUES
('QCR-2026-1001', 'PO-2026-0001', 'PART-A320-FP-01', 'SUP-001', 'COMBINED', 1, 'SUBMITTED', 'E2001');

INSERT INTO dbo.qc_report_version(report_id, version_no, inspector_emp_id, result, findings_json, failure_cause, signature_ref, is_approved_snapshot)
VALUES
('QCR-2026-1001', 1, 'E2001', 'PASS', '{"dimensional":{"status":"PASS"},"ndt":{"status":"PASS"}}', NULL, 'https://secure-sign.example/aeronetb/reports/QCR-2026-1001-v1.sig', 0);

INSERT INTO dbo.component_certification(certification_id, report_id, part_id, supplier_id, inspector_emp_id, certification_status, cert_payload_json, issued_at)
VALUES
('CERT-2026-9001', 'QCR-2026-1001', 'PART-A320-FP-01', 'SUP-001', 'E2001', 'UNDER_REVIEW', '{"materialTrace":"pending","stamp":"draft"}', '2026-03-03 12:00:00');

INSERT INTO dbo.certification_material_trace(certification_id, raw_material_batch_no, origin_country, mill_certificate_ref, trace_notes)
VALUES
('CERT-2026-9001', 'BATCH-TI-33881', 'Germany', 'mill://certs/ti33881.pdf', 'Titanium lot verified');

INSERT INTO dbo.facility(facility_id, facility_name, country, city)
VALUES
('FAC-ATH-01', 'Athens Plant 1', 'Greece', 'Athens');

INSERT INTO dbo.equipment_asset(equipment_id, facility_id, equipment_name, equipment_type, status, last_maintenance_at, next_maintenance_due)
VALUES
('EQ-CNC-01', 'FAC-ATH-01', 'CNC Machining Center A', 'CNC', 'OK', '2026-02-01 08:00:00', '2026-05-01 08:00:00');

INSERT INTO dbo.iot_device(device_id, equipment_id, shipment_id, sensor_type, unit, threshold_min, threshold_max, is_active)
VALUES
('DEV-TEMP-01', 'EQ-CNC-01', NULL, 'TEMPERATURE', 'C', 5.0000, 70.0000, 1),
('DEV-GPS-01', NULL, 'SHIP-0001', 'GPS', 'deg', NULL, NULL, 1);

INSERT INTO dbo.sensor_reading(device_id, observed_at, reading_value, latitude, longitude, raw_payload_json)
VALUES
('DEV-TEMP-01', '2026-03-03 10:00:00.000', 52.30000, NULL, NULL, '{"temp":52.3,"unit":"C"}'),
('DEV-GPS-01', '2026-03-03 10:00:00.000', NULL, 38.423733, 27.142826, '{"lat":38.423733,"lon":27.142826}');

INSERT INTO dbo.audit_log(emp_id, action_type, entity_name, entity_key, access_outcome, before_state_json, after_state_json, source_ip, user_agent)
VALUES
('E1001', 'INSERT', 'purchase_order', 'PO-2026-0001', 'SUCCESS', NULL, '{"status":"DISPATCHED"}', '10.0.0.25', 'DashboardWeb/1.0'),
('E2001', 'INSERT', 'qc_report', 'QCR-2026-1001', 'SUCCESS', NULL, '{"result":"PASS"}', '10.0.0.31', 'DashboardWeb/1.0');

/* Example: finalize certification to become immutable */
UPDATE dbo.component_certification
SET certification_status = 'APPROVED',
	approved_finalized_at = SYSUTCDATETIME()
WHERE certification_id = 'CERT-2026-9001';

