// SPDX-License-Identifier: GPL-2.0
/*
 * predator_wmi - tiny bridge to the Acer "AcerGamingFunction" ACPI-WMI
 * interface (GUID 7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56, method WMBH),
 * the same interface PredatorSense (PSAdminAgent.exe / PSSvc.exe) uses on
 * Windows for SetGamingLedBehavior / SetGamingRgbSetting.
 *
 * Interface:  /sys/kernel/predator_wmi/call
 *   write: "<method_id> <hex_value> [input_size_bytes]"   (size default 8)
 *   read:  result of the last call:  "method=<id> in=0x... out=0x... status=<acpi_status>"
 *
 * By default only the lighting methods (5..8 on the PO5-640 BIOS:
 * Set/Get LED behaviour, Set/Get RGB) are accepted, so the sysfs file can be
 * handed to an unprivileged group.  Load with allow_any_method=1 to probe.
 *
 * All protocol knowledge lives in the userspace tool (predator-led).
 */
#include <linux/module.h>
#include <linux/kobject.h>
#include <linux/sysfs.h>
#include <linux/acpi.h>
#include <linux/wmi.h>
#include <linux/mutex.h>
#include <linux/slab.h>

#define ACER_GAMING_GUID "7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"

static bool allow_any_method;
module_param(allow_any_method, bool, 0444);
MODULE_PARM_DESC(allow_any_method, "allow every WMBH method id, not only lighting 5..8 (default: no)");

static struct kobject *pw_kobj;
static DEFINE_MUTEX(pw_lock);
static u32 last_method;
static u64 last_in, last_out;
static int last_out_type = -1;
static acpi_status last_status = AE_OK;

static ssize_t call_show(struct kobject *k, struct kobj_attribute *a, char *buf)
{
	ssize_t n;

	mutex_lock(&pw_lock);
	n = sysfs_emit(buf, "method=%u in=0x%016llx out=0x%016llx out_type=%d status=%s\n",
		       last_method, last_in, last_out, last_out_type,
		       acpi_format_exception(last_status));
	mutex_unlock(&pw_lock);
	return n;
}

static ssize_t call_store(struct kobject *k, struct kobj_attribute *a,
			  const char *buf, size_t count)
{
	struct acpi_buffer out = { ACPI_ALLOCATE_BUFFER, NULL };
	struct acpi_buffer in;
	union acpi_object *obj;
	unsigned int method, size = 8;
	unsigned long long value;
	acpi_status st;
	u64 inval;
	int n;

	n = sscanf(buf, "%u %llx %u", &method, &value, &size);
	if (n < 2 || (size != 4 && size != 8))
		return -EINVAL;
	if (!allow_any_method && (method < 5 || method > 8))
		return -EPERM;

	inval = value;
	in.length = size;
	in.pointer = &inval;	/* little-endian: low 4 bytes when size == 4 */

	mutex_lock(&pw_lock);
	st = wmi_evaluate_method(ACER_GAMING_GUID, 0, method, &in, &out);
	last_method = method;
	last_in = value;
	last_out = 0;
	last_out_type = -1;
	last_status = st;
	obj = out.pointer;
	if (ACPI_SUCCESS(st) && obj) {
		last_out_type = obj->type;
		if (obj->type == ACPI_TYPE_INTEGER) {
			last_out = obj->integer.value;
		} else if (obj->type == ACPI_TYPE_BUFFER) {
			u32 l = min_t(u32, obj->buffer.length, sizeof(u64));

			memcpy(&last_out, obj->buffer.pointer, l);
		}
	}
	mutex_unlock(&pw_lock);
	kfree(obj);

	return ACPI_SUCCESS(st) ? count : -EIO;
}

static struct kobj_attribute call_attr = __ATTR(call, 0600, call_show, call_store);

static int __init pw_init(void)
{
	int ret;

	if (!wmi_has_guid(ACER_GAMING_GUID)) {
		pr_err("predator_wmi: AcerGamingFunction WMI GUID not present\n");
		return -ENODEV;
	}
	pw_kobj = kobject_create_and_add("predator_wmi", kernel_kobj);
	if (!pw_kobj)
		return -ENOMEM;
	ret = sysfs_create_file(pw_kobj, &call_attr.attr);
	if (ret)
		kobject_put(pw_kobj);
	return ret;
}

static void __exit pw_exit(void)
{
	sysfs_remove_file(pw_kobj, &call_attr.attr);
	kobject_put(pw_kobj);
}

module_init(pw_init);
module_exit(pw_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Raw access to Acer AcerGamingFunction WMI (Predator Orion lighting)");
