const fs=require('fs');
async function testExport() {
  const loginRes = await fetch('http://127.0.0.1:8000/api/v1/auth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'username=admin@alinex.com&password=adminpassword'
  });
  const loginData = await loginRes.json();
  const token = loginData.access_token;
  
  const res = await fetch('http://127.0.0.1:8000/api/v1/forms/workbench/export/json', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      module: 'sales_order',
      document_type: 'sales_order',
      form_data: { sales_order_no: 'SO-123' },
      item_rows: [],
      metadata: { dataset_name: 'test' }
    })
  });
  const text = await res.text();
  console.log('Status:', res.status);
  console.log('Body:', text);
}
testExport();
